from fastapi import FastAPI, Depends
from models import AgentTrace
from database import get_session, init_db
from sqlalchemy.ext.asyncio import AsyncSession

app = FastAPI(title="Agent Control Room")


@app.on_event("startup")
async def on_startup():
    """
    Initialize the database on application startup.
    Creates all tables if they don't exist.
    """
    await init_db()


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
    return {"message": "Control agent room - Phase 1: Log Engine", "status": "operational"}
