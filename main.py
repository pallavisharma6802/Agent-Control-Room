from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from models import AgentTrace
from database import get_session, init_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from agent import GeminiService
import os
from typing import Optional

app = FastAPI(title="LLM Sentinel - Hallucination Detection API")

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
    
    Deduplication: If the same response_text is encountered (e.g., duplicate job),
    it will be silently ignored to prevent duplicate records.
    
    Returns the recorded trace ID for reference.
    """
    try:
        # Ensure timestamp is a datetime object if it comes as a string
        if isinstance(trace.timestamp, str):
            from datetime import datetime
            trace.timestamp = datetime.fromisoformat(trace.timestamp.replace('Z', '+00:00'))
        
        session.add(trace)
        await session.commit()
        await session.refresh(trace)
        return {"status": "recorded", "id": trace.id, "duplicate": False}
    except IntegrityError:
        # Duplicate entry - roll back and return gracefully
        await session.rollback()
        return {"status": "duplicate", "id": None, "duplicate": True}


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "message": "LLM Sentinel - Hallucination Detection & Observability",
        "status": "operational",
        "gemini_configured": gemini_service is not None
    }


@app.get("/stats")
async def get_stats(session: AsyncSession = Depends(get_session)):
    """
    Returns aggregate hallucination detection statistics.
    Used by the dashboard to show live metrics.
    """
    from sqlalchemy import select, func, case
    
    result = await session.execute(
        select(
            func.count(AgentTrace.id).label("total_traces"),
            func.sum(case((AgentTrace.is_hallucinated == True, 1), else_=0)).label("hallucinated_count"),
            func.avg(
                case((AgentTrace.is_hallucinated == True, 1), else_=0)
            ).label("hallucination_rate"),
            func.count(AgentTrace.session_id.distinct()).label("unique_sessions"),
        )
    )
    row = result.one()
    
    # Get recent traces for timeline
    recent = await session.execute(
        select(AgentTrace.timestamp, AgentTrace.is_hallucinated, AgentTrace.session_id)
        .order_by(AgentTrace.timestamp.desc())
        .limit(20)
    )
    recent_traces = [
        {
            "timestamp": t.timestamp.isoformat(),
            "is_hallucinated": t.is_hallucinated,
            "session_id": t.session_id
        }
        for t in recent.all()
    ]
    
    return {
        "total_traces": row.total_traces or 0,
        "hallucinated_count": int(row.hallucinated_count or 0),
        "hallucination_rate": round(float(row.hallucination_rate or 0) * 100, 1),
        "unique_sessions": row.unique_sessions or 0,
        "recent_traces": recent_traces
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


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """
    Serve the interactive hallucination detection dashboard.
    Dark terminal-themed monitoring interface.
    """
    try:
        with open("dashboard.html") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dashboard file not found")


@app.get("/eval_results.json")
async def eval_results():
    """
    Serve the evaluation results JSON for the dashboard chart.
    """
    return FileResponse("eval_results.json")
