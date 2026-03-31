# API Failure Debugger

A developer tool that takes a failed API request (status code, headers, payload, logs) and returns a plain-English diagnosis: what went wrong, why, and how to fix it.

Built over a weekend as a practical utility: rule-based diagnosis is the primary source of truth, with an optional AI-style suggestion layer for improved explanations (heuristics for now).

---

## Why I built this

I kept copy-pasting failed API responses into Slack asking colleagues "what does this mean?". Most of the time it was one of six things: expired token, missing field, wrong endpoint, permissions, server crash, or timeout. So I wrote a small tool that checks those six cases and tells you which one it is.

---

## Architecture

```
Request (JSON)
     │
     ▼
 validator.py         ← sanity check the input (bad endpoint format, etc.)
     │
     ▼
 log_parser.py        ← regex scan of log text → list of flags ["expired_token", "timeout", ...]
     │
     ▼
debugger.py          ← rule engine: status code + flags + error msg → diagnosis
     │
     ▼
ai_suggester.py      ← heuristic AI enhancements (optional)
     │
     ▼
cache (TTL + LRU-ish) ← skip compute + DB writes on repeats
     │
     ▼
database.py          ← SQLite insert (retry + deduplication)
     │
     ▼
Response (JSON)       ← root_cause, confidence_score, suggested_fix, corrected_request (+ AI fields)
```

The rule engine is just an `if/elif` block in `debugger.py`. No fancy registry pattern, no plugin system. Easy to read, easy to explain, easy to extend — add a new `elif` and a handler function.

The request path also includes: negation-aware `log_parser.py`, input size limits in `validator.py`, a secondary `ai_suggester.py` enhancement layer, and an in-memory TTL cache to avoid recomputing identical failures.

---

## Features

- Rule-based diagnosis in `debugger.py` (explainable `if/elif` engine)
- AI-enhanced explanation/suggestions fields:
  - `ai_explanation`
  - `additional_suggestions`
  - `ai_confidence_note`
- Request caching with deduplication (in-memory TTL + LRU-style eviction)
- Rate limiting on `/debug` and `/api/v1/debug` (slowapi)
- Request ID tracing for debugging (`X-Request-ID` response header)
- Environment-based configuration via `.env` + `python-dotenv` (`app/config.py`)
- Improved log parsing with negation awareness (avoid “not expired” false positives)
- Input validation with size limits (endpoint/error/log/payload/header bounds)
- Database retry + logging + deduplication window to reduce repeated inserts
- Frontend: dark terminal UI, AI fields display, copy corrected request, and a history/export view

---

## Design Decisions

**Why rule-based instead of ML?**
The failure patterns are well-defined and finite. A 401 with "JWT expired" in the logs is always the same problem. ML would add complexity and unpredictability for zero benefit here. Rule-based is also easier to audit and explain.

**Why SQLite?**
It's a developer tool, not a SaaS product. SQLite is zero-config and works inside Docker without any setup. Swap it for Postgres if you ever need concurrent writes.

**Why no ORM?**
The schema is one table with six columns. Writing raw `sqlite3` is 10 lines. An ORM would be more code, not less.

**Why caching + DB dedup?**
Debugging often repeats the same failed calls while iterating on the fix. The in-memory TTL cache avoids recomputing identical inputs, and SQLite deduplication skips repeated inserts within a time window.

**Why rate limiting?**
The debug endpoint is safe for humans but not for accidental request storms. `slowapi` keeps it predictable and protects the server.

**Why request IDs?**
`X-Request-ID` makes it easier to correlate a single debug response with server logs when investigating incidents.

---

## Setup

### Local (Python 3.11+)

```bash
git clone <repo>
cd api-debugger
cp .env.example .env
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000

### Docker

```bash
docker build -t api-debugger .
docker run -p 8000:8000 api-debugger
```

---

## API Usage

### POST /debug

```bash
curl -X POST http://localhost:8000/debug \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint": "/api/v1/users",
    "method": "POST",
    "headers": {"Authorization": "Bearer token"},
    "payload": {"name": "John"},
    "status_code": 400,
    "error_message": "Missing required field: email",
    "logs": ""
  }'
```

Rate-limited and returns a `X-Request-ID` response header.

Response:
```json
{
  "root_cause": "Request is missing required field(s): email.",
  "confidence_score": 0.87,
  "suggested_fix": "Add the missing field(s) to the request body: email. Check the API schema for their expected types.",
  "corrected_request": {
    "endpoint": "/api/v1/users",
    "method": "POST",
    "headers": {"Authorization": "Bearer token"},
    "payload": {"name": "John", "email": "<email_value>"}
  },
  "issue_type": "bad_request",
  "log_flags": [],
  "ai_explanation": "In plain terms: ...",
  "additional_suggestions": [
    "Try validating your payload against the API schema before sending.",
    "Use Postman or curl to isolate whether the issue is client-side or server-side."
  ],
  "ai_confidence_note": "Moderate confidence — likely correct, but verify against your specific setup."
}
```

### POST /api/v1/debug

Alias of `/debug` (same request/response behavior, rate-limited, and returns `X-Request-ID`).

### GET /history

Returns the last 20 debug calls stored in SQLite.

### GET /health

```json
{"status": "ok"}
```

---

## Sample Inputs

**Expired token:**
```json
{
  "endpoint": "/api/v1/profile",
  "method": "GET",
  "headers": {"Authorization": "Bearer eyJhbGc.expired"},
  "status_code": 401,
  "error_message": "Unauthorized",
  "logs": "JWT expired at 2024-01-15 10:45:00"
}
```

**Wrong endpoint path:**
```json
{
  "endpoint": "/users/profile",
  "method": "GET",
  "headers": {"Authorization": "Bearer valid_tok"},
  "status_code": 404,
  "error_message": "Not Found",
  "logs": ""
}
```

**Timeout:**
```json
{
  "endpoint": "/api/v1/reports/generate",
  "method": "POST",
  "headers": {"Authorization": "Bearer valid_tok"},
  "payload": {"from": "2024-01-01", "to": "2024-12-31"},
  "status_code": 504,
  "error_message": "Gateway Timeout",
  "logs": "upstream timed out (110: Connection timed out)"
}
```

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Limitations

- The cache is in-memory per process; it is not shared across multiple Gunicorn/Uvicorn workers.
- The “AI” layer is heuristic-based for now (no external LLM call).
- Deduplication uses time windows and the current SQLite schema; it is best-effort.

---

## Future Improvements

- Redis cache + deduplication across multiple instances
- Replace the heuristic `ai_suggester.py` layer with an optional real LLM call
- Better scaling for concurrent history writes (connection pooling / batching)
- Add more specific pattern matching for common frameworks (Django REST, Express error formats)
- Export debug history as CSV and improve the frontend history/export UX
