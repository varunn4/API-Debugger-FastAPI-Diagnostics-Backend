import hashlib
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi import FastAPI, HTTPException, Response
from starlette.requests import Request as StarletteRequest
from pathlib import Path
import uvicorn

from app.models import DebugRequest, DebugResponse
from app.debugger import debug
from app.log_parser import parse_logs
from app.validator import validate
from app.database import init_db, save_debug_result, recent_logs
from app.ai_suggester import enhance_with_ai
from app.config import (
    CACHE_MAX_SIZE,
    CACHE_TTL_MINUTES,
    DEBUG_RATE_LIMIT,
    LOG_LEVEL,
)

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

app = FastAPI(
    title="API Failure Debugger",
    description="Paste a failed API call and get a plain-English diagnosis.",
    version="1.0.0",
)

# --- logging ---
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# --- rate limiting ---
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def _rate_limit_exceeded_handler(request: StarletteRequest, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})


# --- in-memory cache (TTL + LRU eviction) ---
_cache_lock = threading.Lock()
_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_cache_ttl_seconds = max(0, CACHE_TTL_MINUTES * 60)


def make_cache_key(req: DebugRequest) -> str:
    """MD5 hash of (endpoint + method + status_code + error_message + logs)."""
    data = {
        "endpoint": req.endpoint,
        "method": req.method.upper(),
        "status_code": req.status_code,
        "error_message": req.error_message or "",
        "logs": req.logs or "",
    }
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def cache_get(key: str) -> dict | None:
    now = time.time()
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None

        expires_at, value = item
        if now > expires_at:
            _cache.pop(key, None)
            return None

        _cache.move_to_end(key)
        return value


def cache_set(key: str, value: dict) -> None:
    expires_at = time.time() + _cache_ttl_seconds
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
        _cache[key] = (expires_at, value)

        # LRU eviction
        while len(_cache) > CACHE_MAX_SIZE:
            _cache.popitem(last=False)


def _handle_debug(req: DebugRequest, response: Response) -> DebugResponse:
    request_id = str(uuid.uuid4())
    response.headers["X-Request-ID"] = request_id

    # basic sanity check before we do anything
    is_valid, problems = validate(req)
    if not is_valid:
        logger.info(
            f"Validation failed request_id={request_id} endpoint={req.endpoint} errors={problems}"
        )
        raise HTTPException(status_code=422, detail={"errors": problems})

    # cache lookup (skip log parsing + rule engine + DB write on hit)
    cache_key = make_cache_key(req)
    cached = cache_get(cache_key)
    if cached is not None:
        logger.info(
            f"Cache hit request_id={request_id} endpoint={req.endpoint} status_code={req.status_code}"
        )
        return DebugResponse(**cached)

    # pull signals from log text (if provided)
    log_flags = parse_logs(req.logs or "")

    # run the rule engine
    result = debug(req, log_flags)

    # AI enhancement layer — improves explanations, adds suggestions (does not replace rule logic)
    result_dict = result.model_dump()
    ai_fields = enhance_with_ai(result_dict, req.model_dump())
    final_result = {**result_dict, **ai_fields}

    # fire and forget — don't let a DB write break the response
    try:
        save_debug_result(req, result)
    except Exception as e:
        logger.error(f"DB save failed for {req.endpoint} request_id={request_id}: {e}")

    cache_set(cache_key, final_result)
    logger.info(
        f"Cache miss request_id={request_id} endpoint={req.endpoint} issue_type={result.issue_type}"
    )
    return DebugResponse(**final_result)

# run DB init on startup
@app.on_event("startup")
def startup():
    init_db()


@app.post("/debug", response_model=DebugResponse)
@limiter.limit(DEBUG_RATE_LIMIT)
def debug_request(req: DebugRequest, response: Response, request: StarletteRequest):
    return _handle_debug(req, response)


@app.post("/api/v1/debug", response_model=DebugResponse)
@limiter.limit(DEBUG_RATE_LIMIT)
def debug_request_v1(req: DebugRequest, response: Response, request: StarletteRequest):
    return _handle_debug(req, response)


@app.get("/history")
def get_history():
    """Last 20 debug calls — useful for spotting patterns."""
    return recent_logs(20)


@app.get("/health")
def health():
    return {"status": "ok"}


# serve the frontend
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/frontend", StaticFiles(directory=str(frontend_dir)), name="frontend")

    @app.get("/")
    def serve_frontend():
        return FileResponse(str(frontend_dir / "index.html"))


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
