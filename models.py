from sqlmodel import SQLModel, Field, JSON, Column
from typing import Optional, Dict, Any
from datetime import datetime


class AgentTrace(SQLModel, table=True):
    """
    Stores the complete trace of an agent's reasoning process.
    The grounding_metadata field uses JSONB for efficient querying of nested sources.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    prompt: str
    response_text: str
    # The 'receipts' - where Gemini got its info
    grounding_metadata: Dict[str, Any] = Field(default={}, sa_column=Column(JSON))
    is_hallucinated: bool = Field(default=False)
