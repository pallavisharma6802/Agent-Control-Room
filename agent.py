from google import genai
from google.genai import types
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dateutil import parser
import re
import httpx
from models import AgentTrace


class GeminiService:
    """
    Service for interacting with Gemini 3 Pro with Google Search grounding.
    Extracts grounding metadata and detects stale knowledge.
    """
    
    def __init__(self, api_key: str, log_endpoint: str = "http://localhost:8000/log-trace"):
        self.client = genai.Client(api_key=api_key)
        self.log_endpoint = log_endpoint
        self.stale_threshold_days = 180  # 6 months
    
    async def get_grounded_response(self, prompt: str, session_id: str) -> Dict[str, Any]:
        """
        Get a grounded response from Gemini and automatically log it.
        
        Args:
            prompt: The user's question/prompt
            session_id: Unique session identifier
            
        Returns:
            Dictionary with response text and metadata
        """
        # Configure Google Search tool for live grounding
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
        
        # Get response from Gemini
        response = self.client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=config
        )
        
        # Extract grounding metadata 
        grounding_metadata = self._extract_grounding_metadata(response)
        
        # Check for hallucination (answer without grounding)
        is_hallucinated = self._detect_hallucination(response, grounding_metadata)
        
        # Check for stale knowledge
        is_stale = self._detect_stale_knowledge(grounding_metadata)
        
        # Calculate confidence score
        confidence_score = self._calculate_confidence_score(grounding_metadata)
        
        # Create trace object
        trace = AgentTrace(
            session_id=session_id,
            prompt=prompt,
            response_text=response.text,
            grounding_metadata=grounding_metadata,
            is_hallucinated=is_hallucinated
        )
        
        # Log to database
        await self._log_trace(trace)
        
        return {
            "response": response.text,
            "grounding_metadata": grounding_metadata,
            "is_hallucinated": is_hallucinated,
            "is_stale": is_stale,
            "sources_count": len(grounding_metadata.get("grounding_chunks", [])),
            "confidence_score": confidence_score,
            "warning": "⚠️ Unverified Data - No grounding supports" if not grounding_metadata.get("grounding_supports") and not is_hallucinated else None
        }
    
    def _extract_grounding_metadata(self, response) -> Dict[str, Any]:
        """
        Extract and structure grounding metadata from Gemini response.
        """
        metadata = {
            "search_queries": [],
            "grounding_chunks": [],
            "grounding_supports": []
        }
        
        try:
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                
                if hasattr(candidate, 'grounding_metadata'):
                    gm = candidate.grounding_metadata
                    
                    # Extract search queries
                    if hasattr(gm, 'search_entry_point'):
                        if hasattr(gm.search_entry_point, 'rendered_content'):
                            metadata["search_queries"].append(
                                gm.search_entry_point.rendered_content
                            )
                    
                    # Extract grounding chunks (sources)
                    if hasattr(gm, 'grounding_chunks'):
                        for chunk in gm.grounding_chunks:
                            chunk_data = {}
                            if hasattr(chunk, 'web'):
                                chunk_data = {
                                    "uri": chunk.web.uri if hasattr(chunk.web, 'uri') else None,
                                    "title": chunk.web.title if hasattr(chunk.web, 'title') else None
                                }
                            metadata["grounding_chunks"].append(chunk_data)
                    
                    # Extract grounding supports (which parts cite which sources)
                    if hasattr(gm, 'grounding_supports'):
                        for support in gm.grounding_supports:
                            support_data = {
                                "segment_text": support.segment.text if hasattr(support, 'segment') else None,
                                "grounding_chunk_indices": list(support.grounding_chunk_indices) if hasattr(support, 'grounding_chunk_indices') else [],
                                "confidence_scores": list(support.confidence_scores) if hasattr(support, 'confidence_scores') else []
                            }
                            metadata["grounding_supports"].append(support_data)
        
        except Exception as e:
            print(f"Error extracting grounding metadata: {e}")
        
        return metadata
    
    def _detect_hallucination(self, response, grounding_metadata: Dict[str, Any]) -> bool:
        """
        Detect if the model hallucinated by checking:
        1. Ghost citations (cites sources that don't exist)
        2. Empty Receipt Check (lists/bullets with no grounding)
        3. Ungrounded claims (long response with no grounding supports)
        4. Missing grounding chunks for substantive answers
        """
        response_text = response.text
        chunks = grounding_metadata.get('grounding_chunks', [])
        source_count = len(chunks)
        supports = grounding_metadata.get('grounding_supports', [])
        
        # Check 1: Ghost Citations - model cites [1], [2] but they don't exist
        found_citations = re.findall(r'\[(\d+)\]', response_text)
        
        if found_citations:
            max_citation = max(int(c) for c in found_citations)
            if max_citation > source_count:
                print(f"⚠️ Hallucination detected: Ghost citation [{max_citation}] (only {source_count} sources exist)")
                return True  # Hallucination: Cited a source that doesn't exist
        
        # Check 2: The "Empty Receipt" Check - lists/bullets with no sources
        # If model gives a list (bullets or numbered) but search returned 0 chunks
        has_list = ("*" in response_text or 
                   re.search(r'\n\d+\.', response_text) or 
                   re.search(r'\n-', response_text))
        
        if source_count == 0 and has_list:
            print(f"⚠️ Hallucination detected: Listed items with zero grounding chunks (Empty Receipt)")
            return True
        
        # Check 3: Ungrounded Claims - long response with no grounding supports
        if len(response_text) > 200 and not supports and source_count == 0:
            print(f"⚠️ Hallucination detected: Long response ({len(response_text)} chars) with no grounding supports")
            return True
        
        # Check 4: Basic check - has substantive answer but no sources at all
        has_answer = response_text and len(response_text) > 50
        has_grounding = source_count > 0
        
        if has_answer and not has_grounding:
            print(f"⚠️ Hallucination detected: Substantive answer with no grounding chunks")
            return True
        
        return False
    
    def _calculate_confidence_score(self, grounding_metadata: Dict[str, Any]) -> float:
        """
        Calculate a confidence score (0-1) based on grounding quality.
        
        Scoring factors:
        - Has grounding chunks: +0.4
        - Has grounding supports: +0.4
        - Number of sources (capped at 5): +0.2
        """
        score = 0.0
        
        chunks = grounding_metadata.get('grounding_chunks', [])
        supports = grounding_metadata.get('grounding_supports', [])
        
        # Factor 1: Has grounding chunks
        if len(chunks) > 0:
            score += 0.4
        
        # Factor 2: Has grounding supports (claim-to-source mapping)
        if len(supports) > 0:
            score += 0.4
        
        # Factor 3: Number of diverse sources (more sources = higher confidence)
        source_bonus = min(len(chunks) / 5.0, 1.0) * 0.2
        score += source_bonus
        
        return round(score, 2)
    
    def _detect_stale_knowledge(self, grounding_metadata: Dict[str, Any]) -> bool:
        """
        Detect if any of the cited sources are stale (older than threshold).
        """
        current_date = datetime.now()
        threshold_date = current_date - timedelta(days=self.stale_threshold_days)
        
        for chunk in grounding_metadata.get("grounding_chunks", []):
            uri = chunk.get("uri", "")
            
            # Try to extract date from URI or title
            date_found = self._extract_date_from_source(uri, chunk.get("title", ""))
            
            if date_found and date_found < threshold_date:
                return True
        
        return False
    
    def _extract_date_from_source(self, uri: str, title: str) -> Optional[datetime]:
        """
        Attempt to extract a date from the URI or title.
        Common patterns: YYYY-MM-DD, YYYY/MM/DD, Month DD, YYYY
        """
        text = f"{uri} {title}"
        
        # Pattern: YYYY-MM-DD or YYYY/MM/DD
        date_patterns = [
            r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})',
            r'(\d{4})[/-](\d{1,2})',
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{1,2}),? (\d{4})'
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    # Try to parse the matched date
                    date_str = match.group(0)
                    return parser.parse(date_str, fuzzy=True)
                except:
                    continue
        
        return None
    
    async def _log_trace(self, trace: AgentTrace):
        """
        Send the trace to the log endpoint.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Exclude id and use json mode for proper datetime serialization
                trace_dict = trace.model_dump(mode='json', exclude={'id'})
                response = await client.post(
                    self.log_endpoint,
                    json=trace_dict
                )
                response.raise_for_status()
        except Exception as e:
            print(f"Failed to log trace: {e}")
