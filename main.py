from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from models import AgentTrace
from database import get_session, init_db
from sqlalchemy.ext.asyncio import AsyncSession
from agent import GeminiService
import os
from typing import Optional

app = FastAPI(title="Agent Control Room")

# Initialize Gemini service (will be configured with API key from env)
gemini_service: Optional[GeminiService] = None


class QueryRequest(BaseModel):
    prompt: str
    session_id: str = "default"


@app.on_event("startup")
async def on_startup():
    """
    Initialize the database on application startup.
    Creates all tables if they don't exist.
    """
    global gemini_service
    await init_db()
    
    # Initialize Gemini service if API key is available
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        gemini_service = GeminiService(api_key=api_key)


@app.post("/log-trace")
async def log_trace(trace: AgentTrace, session: AsyncSession = Depends(get_session)):
    """
    Log an agent's reasoning trace to the database.
    This endpoint receives the complete trace including:
    - The original prompt
    - The model's response
    - Grounding metadata (sources/URIs)
    - Hallucination flag
    Returns the recorded trace ID for reference.
    """
    session.add(trace)
    await session.commit()
    await session.refresh(trace)
    return {"status": "recorded", "id": trace.id}


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "message": "Agent Control Room - Phase 2: The Sentinel",
        "status": "operational",
        "gemini_configured": gemini_service is not None
    }


@app.post("/query")
async def query_agent(request: QueryRequest):
    """
    Query the Gemini agent with Google Search grounding.
    Automatically logs the trace with grounding metadata.
    
    Returns:
        - response: The agent's answer
        - grounding_metadata: Sources and search queries used
        - is_hallucinated: Whether the answer lacks grounding
        - is_stale: Whether sources are outdated
    """
    if not gemini_service:
        raise HTTPException(
            status_code=503,
            detail="Gemini service not configured. Set GEMINI_API_KEY environment variable."
        )
    
    result = await gemini_service.get_grounded_response(
        prompt=request.prompt,
        session_id=request.session_id
    )
    
    return result
