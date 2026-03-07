# LLM Sentinel — Agent Control Room

A production-grade observability platform for LLM agents. Queries **Gemini 2.5 Pro** with Google Search grounding, runs **10 hallucination detection checks** on every response, tracks source freshness, and logs all interactions to PostgreSQL. A Next.js dashboard surfaces live metrics and eval results in real time.

> **Live stats:** 1,044 traces logged · 42.0% hallucination detection rate · 306 unique sessions

---

## What It Does

Most LLM observability tools tell you _what_ a model said. LLM Sentinel tells you _whether to trust it_.

Every query goes through a grounding pipeline that:

1. Retrieves Google Search results alongside the Gemini response
2. Cross-references the response content against grounding sources
3. Flags hallucinations using 10 targeted checks (citation ghosts, semantic mismatches, unverified numbers, recency drift, and more)
4. Scores confidence (0–1) based on grounding depth
5. Persists the full trace to PostgreSQL for trend analysis

---

## Architecture

```
User / Dashboard
      │
      ▼
  FastAPI (port 8000)
      │
      ├── POST /query ──► GeminiService
      │                        │
      │                  Google Search grounding
      │                        │
      │                  Extract grounding metadata
      │                        │
      │                  Run 10 hallucination checks
      │                        │
      │                  Detect stale sources (>6 months)
      │                        │
      │                  Write trace → PostgreSQL
      │                        │
      │                  Return response + metadata
      │
      ├── GET  /stats    ──► Aggregate metrics from DB
      ├── GET  /          ──► Health check
      └── POST /log-trace ──► Manual trace ingestion

  Next.js Dashboard (port 3000)
      │  rewrites /api/* → FastAPI
      └── Live stats · eval accuracy chart · "Try It Live" panel

  Apache Airflow (port 8080)
      └── sentinel_eval DAG (hourly) — fires labelled test prompts
```

---

## Tech Stack

| Layer      | Technology                                 |
| ---------- | ------------------------------------------ |
| API        | FastAPI + Uvicorn                          |
| LLM        | Gemini 2.5 Pro (google-genai SDK)          |
| ORM        | SQLModel (SQLAlchemy + Pydantic)           |
| Database   | PostgreSQL 13                              |
| Dashboard  | Next.js 14 · Tailwind CSS · Recharts · SWR |
| Scheduler  | Apache Airflow 2 (LocalExecutor)           |
| Containers | Docker Compose                             |

---

## Hallucination Detection

Ten checks run on every response, each returning a `snake_case` reason code:

| Code                            | Trigger                                                     |
| ------------------------------- | ----------------------------------------------------------- |
| `ghost_citation`                | Response cites `[N]` but fewer than N sources exist         |
| `empty_receipt`                 | Bullet/numbered list returned with zero grounding chunks    |
| `ungrounded_claim`              | Response >200 chars with no supports and no chunks          |
| `missing_grounding`             | Substantive answer (>50 chars) with no sources at all       |
| `weak_technical_grounding`      | Technical language present but fewer than 6 sources         |
| `suspicious_certainty`          | High-certainty language with fewer than 3 sources           |
| `named_system_detection`        | Specific named system described with ≤12 generic sources    |
| `semantic_mismatch`             | Response content has <30% word overlap with grounding texts |
| `ungrounded_quantitative_claim` | >50% of specific numbers in response not found in sources   |
| `recency_mismatch`              | Recent event claims but all sources older than 3 months     |

A `confidence_score` (0–1) is returned alongside each response, based on grounding chunk presence (+0.4), grounding supports (+0.4), and source count up to 5 (+0.2).

---

## Quickstart

### Docker Compose (recommended)

```bash
# 1. Add your API key
echo "GEMINI_API_KEY=your_key_here" > .env

# 2. Start everything
docker compose up --build

# Services:
#   FastAPI   → http://localhost:8000
#   Dashboard → http://localhost:3000
#   Airflow   → http://localhost:8080  (admin / admin123)
#   Postgres  → localhost:5432
```

### Local Dev (no Docker)

```bash
# 1. Configure environment
GEMINI_API_KEY=your_key_here
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/sentinel_db

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Start Postgres
docker run --name sentinel-db \
  -e POSTGRES_USER=user -e POSTGRES_PASSWORD=pass -e POSTGRES_DB=sentinel_db \
  -p 5432:5432 -d postgres:13

# 4. Run the API
uvicorn main:app --reload

# 5. (Optional) Start the dashboard
cd dashboard && npm install && npm run dev
```

---

## API Reference

### `POST /query`

Query Gemini with Search grounding. Trace is automatically persisted.

```json
// Request
{ "prompt": "Who is the current CEO of OpenAI?", "session_id": "my-session" }

// Response
{
  "response": "Sam Altman is...",
  "grounding_metadata": {
    "search_queries": ["..."],
    "grounding_chunks": [{ "uri": "...", "title": "..." }],
    "grounding_supports": [{ "segment_text": "...", "confidence_scores": [0.9] }]
  },
  "is_hallucinated": false,
  "detection_reason": null,
  "is_stale": false,
  "sources_count": 5,
  "confidence_score": 0.84,
  "warning": null
}
```

### `GET /stats`

Returns aggregate metrics consumed by the dashboard.

### `POST /log-trace`

Manually ingest a trace. Duplicate responses (identical content, matched by MD5) are silently deduplicated.

### `GET /`

Health check — reports service status and whether `GEMINI_API_KEY` is configured.

---

## Evaluation

Runs labelled prompts against the live API and writes `eval_results.json`, which the dashboard reads for the **Evaluation Category Accuracy** chart.

```bash
python eval_runner.py
```

### Results (2026-03-07) — v2 detector · 10 checks

**Overall accuracy: 69.0% (60 / 87 prompts)** · +4.2 pp vs v1 (7 checks)

| Category             | Accuracy | Correct / Total | Δ vs v1  |
| -------------------- | -------- | --------------- | -------- |
| `fabricated_entity`  | **100%** | 12 / 12         | +50 pp   |
| `well_known_fact`    | 78.3%    | 18 / 23         | -21.7 pp |
| `current_state`      | 75.0%    | 12 / 16         | -25 pp   |
| `recent_events`      | 55.6%    | 10 / 18         | +33.6 pp |
| `quantitative_claim` | 44.4%    | 8 / 18          | -2.6 pp  |

**What changed in v2:**

- `semantic_mismatch` — cross-references response text against grounding content directly; drove `fabricated_entity` to 100%
- `recency_mismatch` — compares claim temporality against source publication dates; more than doubled `recent_events` accuracy
- `ungrounded_quantitative_claim` — flags numbers not traceable to any grounding source; minimal impact so far (see known limitations)

**Known limitations:**

- `well_known_fact` and `current_state` regressions reflect false positives introduced by stricter checks — the detector now occasionally flags well-grounded responses that don't meet the tighter thresholds. Threshold tuning is the next priority.
- `quantitative_claim` remains difficult: when Search returns sources containing similar but not identical numbers, the check lacks sufficient signal. A dedicated numeric extraction + normalization step is planned.
- `recent_events` improvement is real but the 55.6% ceiling reflects a fundamental constraint — Gemini's Search grounding retrieves _something_ to cite for most queries, including unverifiable ones. Claim-level source verification (not just presence checking) is needed to push further.

---

## Production Statistics (2026-03-07)

| Metric                  | Value |
| ----------------------- | ----- |
| Total traces            | 1,044 |
| Hallucinations detected | 439   |
| Detection rate          | 42.0% |
| Unique sessions         | 306   |

Detection rate increased from 39.1% (v1) to 42.0% (v2) following the addition of the three new checks. Top detection reasons in production: `named_system_detection`, `weak_technical_grounding`, `suspicious_certainty`.

---

## Database

Single table: `agenttrace`

| Column               | Type     | Notes                                   |
| -------------------- | -------- | --------------------------------------- |
| `id`                 | int PK   | auto                                    |
| `session_id`         | str      | indexed                                 |
| `timestamp`          | datetime | UTC                                     |
| `prompt`             | str      |                                         |
| `response_text`      | str      | unique (MD5 hash index — deduplication) |
| `grounding_metadata` | JSON     | sources, supports, search queries       |
| `is_hallucinated`    | bool     |                                         |
| `detection_reason`   | str?     | `snake_case` code or null               |

```bash
# Inspect recent traces
docker exec -it sentinel-db psql -U user -d sentinel_db \
  -c "SELECT id, session_id, is_hallucinated, detection_reason, timestamp \
      FROM agenttrace ORDER BY timestamp DESC LIMIT 10;"
```
