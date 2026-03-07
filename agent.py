from google import genai
from google.genai import types
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from dateutil import parser
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
import re
from models import AgentTrace


class GeminiService:
    """
    Service for interacting with Gemini 2.5 Pro with Google Search grounding.
    Extracts grounding metadata and detects stale knowledge.
    """

    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        self.stale_threshold_days = 180  # 6 months
    
    async def get_grounded_response(
        self,
        prompt: str,
        session_id: str,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Get a grounded response from Gemini and automatically log it.

        Args:
            prompt: The user's question/prompt
            session_id: Unique session identifier
            db: Active database session for persisting the trace

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
        is_hallucinated, detection_reason = self._detect_hallucination(response, grounding_metadata)
        
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
            is_hallucinated=is_hallucinated,
            detection_reason=detection_reason
        )
        
        # Persist trace directly — no HTTP round-trip needed
        await self._log_trace(trace, db)
        
        return {
            "response": response.text,
            "grounding_metadata": grounding_metadata,
            "is_hallucinated": is_hallucinated,
            "detection_reason": detection_reason,
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
    
    def _detect_hallucination(self, response, grounding_metadata: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Detect if the model hallucinated by checking:
        1. Ghost citations (cites sources that don't exist)
        2. Empty Receipt Check (lists/bullets with no grounding)
        3. Ungrounded claims (long response with no grounding supports)
        4. Missing grounding chunks for substantive answers
        5. Weak grounding (few sources for complex claims)
        6. Suspicious certainty markers
        7. Named system detection
        8. Semantic mismatch (response content not reflected in grounding supports)
        9. Ungrounded quantitative claims (specific numbers not in sources)
        10. Recency mismatch (recent event claims with stale sources)
        
        Returns:
            tuple: (is_hallucinated, detection_reason)
                - is_hallucinated: bool indicating if hallucination detected
                - detection_reason: Optional[str] with snake_case reason or None
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
                return True, "ghost_citation"  # Hallucination: Cited a source that doesn't exist
        
        # Check 2: The "Empty Receipt" Check - lists/bullets with no sources
        # If model gives a list (bullets or numbered) but search returned 0 chunks
        has_list = ("*" in response_text or 
                   re.search(r'\n\d+\.', response_text) or 
                   re.search(r'\n-', response_text))
        
        if source_count == 0 and has_list:
            print(f"⚠️ Hallucination detected: Listed items with zero grounding chunks (Empty Receipt)")
            return True, "empty_receipt"
        
        # Check 3: Ungrounded Claims - long response with no grounding supports
        if len(response_text) > 200 and not supports and source_count == 0:
            print(f"⚠️ Hallucination detected: Long response ({len(response_text)} chars) with no grounding supports")
            return True, "ungrounded_claim"
        
        # Check 4: Basic check - has substantive answer but no sources at all
        has_answer = response_text and len(response_text) > 50
        has_grounding = source_count > 0
        
        if has_answer and not has_grounding:
            print(f"⚠️ Hallucination detected: Substantive answer with no grounding chunks")
            return True, "missing_grounding"
        
        # Check 5: Weak grounding for specific/technical claims
        # If response mentions research papers, specific architectures, or technical details
        # but has very few sources (< 6), likely hallucinating with generic sources
        technical_markers = ['paper', 'research', 'study', 'published', 'architecture', 
                           'framework', 'benchmark', 'technical report', 'whitepaper',
                           'proposed by', 'developed by', 'researchers at', 'methodology']
        
        has_technical_claim = any(marker in response_text.lower() for marker in technical_markers)
        
        if has_technical_claim and source_count < 6 and len(response_text) > 100:
            print(f"⚠️ Hallucination detected: Technical claims with only {source_count} sources (needs 6+)")
            return True, "weak_technical_grounding"
        
        # Check 6: Suspicious certainty with weak grounding
        # If model is very certain but has weak support
        certainty_markers = ['specifically', 'exactly', 'precisely', 'according to', 
                           'the paper states', 'the study found', 'researchers discovered',
                           'researchers proposed', 'the research shows']
        
        has_certainty = any(marker in response_text.lower() for marker in certainty_markers)
        
        if has_certainty and source_count < 3:
            print(f"⚠️ Hallucination detected: High certainty language with only {source_count} sources")
            return True, "suspicious_certainty"
        
        # Check 7: Suspiciously detailed claims about specific named systems/models
        # If response describes specific technical details about a named system
        # but has moderate source count (6-12), might be generic results not specific paper
        specific_system_pattern = r'(?:the|a)\s+\w+(?:Sync|Path|Bridge|Mesh|Probe|Mind|Chain|Flux|Graph|Weave|Cast|Net|Flow|Link)\b'
        has_specific_system = re.search(specific_system_pattern, response_text, re.IGNORECASE)
        
        if has_specific_system and 1 <= source_count <= 12 and len(response_text) > 150:
            print(f"⚠️ Hallucination detected: Specific named system with {source_count} generic sources")
            return True, "named_system_detection"
        
        # Check 8: Semantic mismatch - response content not reflected in grounding supports
        # Extract key content words from response and check if they appear in support segments
        if supports and len(response_text) > 100:
            # Get all support segment texts
            support_texts = ' '.join([s.get('segment_text', '') for s in supports if s.get('segment_text')])
            
            # Extract meaningful words from response (exclude common words)
            response_words = set(re.findall(r'\b[A-Z][a-z]{3,}\b|\b[a-z]{5,}\b', response_text))
            stopwords = {'with', 'from', 'this', 'that', 'have', 'been', 'their', 'which', 'would', 'could', 'should'}
            response_words = response_words - stopwords
            
            if len(response_words) > 10:
                # Check what percentage of key words appear in support texts
                words_in_support = sum(1 for word in response_words if word.lower() in support_texts.lower())
                overlap_ratio = words_in_support / len(response_words)
                
                # If less than 30% overlap, likely hallucinating details
                if overlap_ratio < 0.3 and len(response_text) > 200:
                    print(f"⚠️ Hallucination detected: Low semantic overlap ({overlap_ratio:.1%}) between response and grounding supports")
                    return True, "semantic_mismatch"
        
        # Check 9: Ungrounded quantitative claims - specific numbers not in sources
        # Extract all numbers from response
        numbers_in_response = re.findall(r'\b\d+(?:\.\d+)?%?|\b\d{4}\b', response_text)  # numbers, percentages, years
        
        if numbers_in_response and supports:
            support_texts = ' '.join([s.get('segment_text', '') for s in supports if s.get('segment_text')])
            
            # Check if numbers appear in support texts
            ungrounded_numbers = [num for num in numbers_in_response if num not in support_texts]
            
            # If response has 3+ specific numbers and more than half are not in sources
            if len(numbers_in_response) >= 3 and len(ungrounded_numbers) / len(numbers_in_response) > 0.5:
                print(f"⚠️ Hallucination detected: {len(ungrounded_numbers)}/{len(numbers_in_response)} quantitative claims not found in sources: {ungrounded_numbers[:3]}")
                return True, "ungrounded_quantitative_claim"
        
        # Check 10: Recency mismatch - recent event claims with stale sources
        # Detect temporal markers suggesting recent events
        recent_markers = ['yesterday', 'today', 'this week', 'this month', 'recently', 'just announced', 
                         'breaking', 'latest', 'current', 'now', 'as of', '2026', '2025']
        
        has_recency_claim = any(marker in response_text.lower() for marker in recent_markers)
        
        if has_recency_claim and chunks:
            # Check if we have date information from sources
            current_date = datetime.now()
            threshold_date = current_date - timedelta(days=90)  # 3 months ago
            
            # Try to extract dates from source URIs/titles
            source_dates = []
            for chunk in chunks:
                date_found = self._extract_date_from_source(
                    chunk.get('uri', ''), 
                    chunk.get('title', '')
                )
                if date_found:
                    source_dates.append(date_found)
            
            # If we have date info and all sources are old (>3 months)
            if source_dates and all(date < threshold_date for date in source_dates):
                oldest = min(source_dates)
                days_old = (current_date - oldest).days
                print(f"⚠️ Hallucination detected: Recent event claim but oldest source is {days_old} days old")
                return True, "recency_mismatch"
        
        return False, None
    
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
    
    async def _log_trace(self, trace: AgentTrace, db: AsyncSession) -> None:
        """
        Persist the trace directly to the database.
        Duplicate responses (same md5 hash) are ignored gracefully.
        """
        try:
            db.add(trace)
            await db.commit()
            await db.refresh(trace)
        except IntegrityError:
            await db.rollback()  # duplicate — silently ignore
        except Exception as e:
            await db.rollback()
            print(f"Failed to log trace: {e}")
