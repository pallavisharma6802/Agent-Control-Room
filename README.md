# Agent Control Room - Sentinel Hub

A real-time observability platform for autonomous AI agents that monitors for hallucinations and stale knowledge.

## Phase 2: The Sentinel ✅

### Features

- **Grounded Responses**: Gemini 3 Pro with Google Search integration
- **Source Tracking**: Automatic extraction of URIs and titles from grounding metadata
- **Hallucination Detection**: Flags responses that lack proper grounding
- **Stale Knowledge Detection**: Identifies outdated sources (>6 months old)
- **Automatic Logging**: Every agent interaction is traced to PostgreSQL

### Setup

1. **Create `.env` file:**

```bash
GEMINI_API_KEY=your_actual_api_key
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/sentinel_db
```

2. **Install dependencies:**

```bash
pip install -r requirements.txt
```

3. **Start PostgreSQL:**

```bash
docker run --name sentinel-db -e POSTGRES_USER=user -e POSTGRES_PASSWORD=pass -e POSTGRES_DB=sentinel_db -p 5432:5432 -d postgres
```

4. **Run the API:**

```bash
uvicorn main:app --reload
```

### API Endpoints

#### `POST /query`

Query the Gemini agent with automatic grounding and logging.

**Request:**

```json
{
  "prompt": "What is the top GitHub repo today?",
  "session_id": "test-session-1"
}
```

**Response:**

```json
{
  "response": "According to recent data...",
  "grounding_metadata": {
    "search_queries": ["top github repositories 2026"],
    "grounding_chunks": [
      {
        "uri": "https://github.com/trending",
        "title": "Trending repositories on GitHub"
      }
    ],
    "grounding_supports": [...]
  },
  "is_hallucinated": false,
  "is_stale": false,
  "sources_count": 3
}
```

#### `POST /log-trace`

Manually log an agent trace (used internally by the agent).

#### `GET /`

Health check and status.

### Testing Phase 2

1. **Visit Swagger UI:** http://localhost:8000/docs

2. **Test the `/query` endpoint:**
   - Ask: "What is the latest news about AI?"
   - Check the response for `grounding_metadata`
   - Verify `is_hallucinated` is `false` (if sources exist)

3. **Check the database:**

```bash
docker exec -it sentinel-db psql -U user -d sentinel_db -c "SELECT id, prompt, is_hallucinated, timestamp FROM agenttrace ORDER BY timestamp DESC LIMIT 5;"
```

### Architecture

```
User Query → FastAPI → GeminiService
                           ↓
                     Google Search Grounding
                           ↓
                     Extract Metadata
                           ↓
                     Detect Hallucination/Staleness
                           ↓
                     Log to PostgreSQL
                           ↓
                     Return Response
```

### Next: Phase 3

- Local Llama 70B auditing with AirLLM
- Apache Airflow for 15-minute polling
- Kubernetes deployment

## Tech Stack

- **FastAPI** - Modern async API framework
- **SQLModel** - Type-safe ORM (Pydantic + SQLAlchemy)
- **PostgreSQL** - Production database with JSONB
- **Gemini 3 Pro** - Cloud LLM with grounding
- **Docker** - Containerization
