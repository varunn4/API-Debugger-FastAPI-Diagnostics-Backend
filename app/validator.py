import json
import re
from typing import List, Tuple

from app.models import DebugRequest


def validate(req: DebugRequest) -> Tuple[bool, List[str]]:
    """
    Quick pre-flight checks on the incoming request.
    Returns (is_valid, list_of_problems).

    Not trying to be exhaustive — just catching the obvious stuff
    that would make the debugger produce garbage results.
    """
    problems = []

    # Input length limits — keep the debugger snappy and avoid runaway payloads.
    # These are intentionally conservative and easy to reason about.
    MAX_ENDPOINT_LEN = 200
    MAX_ERROR_MESSAGE_LEN = 2000
    MAX_LOGS_LEN = 20000

    if not req.endpoint or not req.endpoint.startswith("/"):
        problems.append("endpoint must start with '/'")
    if req.endpoint and len(req.endpoint) > MAX_ENDPOINT_LEN:
        problems.append(f"endpoint is too long (max {MAX_ENDPOINT_LEN} chars)")

    if req.method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        problems.append(f"unknown HTTP method: {req.method}")

    if req.status_code < 100 or req.status_code > 599:
        problems.append(f"status_code {req.status_code} is not a valid HTTP status")

    if req.error_message and len(req.error_message) > MAX_ERROR_MESSAGE_LEN:
        problems.append(f"error_message is too long (max {MAX_ERROR_MESSAGE_LEN} chars)")

    if req.logs and len(req.logs) > MAX_LOGS_LEN:
        problems.append(f"logs is too long (max {MAX_LOGS_LEN} chars)")

    # if they gave us a payload, make sure it's actually an object
    # (sometimes people paste a string and wonder why nothing works)
    if req.payload is not None and not isinstance(req.payload, dict):
        problems.append("payload must be a JSON object, not an array or primitive")

    # If they include huge payload/header JSON, reject it early.
    # This prevents pathological inputs from slowing down regex/log parsing.
    try:
        if req.payload is not None:
            payload_size = len(json.dumps(req.payload, ensure_ascii=False))
            if payload_size > 200_000:
                problems.append("payload is too large")
        if req.headers is not None:
            headers_size = len(json.dumps(req.headers, ensure_ascii=False))
            if headers_size > 100_000:
                problems.append("headers are too large")
    except Exception:
        # If serialization fails for any reason, let the downstream validation catch it.
        pass

    return len(problems) == 0, problems


def extract_missing_fields(error_message: str) -> List[str]:
    """
    Try to pull field names out of error messages like:
      "Missing required field: email"
      "Required fields: name, email, phone"
      "Field 'username' is required"
    """
    if not error_message:
        return []

    fields = []

    # "missing required field: email"
    m = re.search(r"missing.{0,20}field[s]?[:\s]+(.+)", error_message, re.IGNORECASE)
    if m:
        raw = m.group(1).strip().rstrip(".")
        fields = [f.strip().strip("'\"") for f in re.split(r"[,;]", raw)]

    # "field 'email' is required"
    for match in re.finditer(r"field[s]?\s+'?([a-z_][a-z0-9_]*)'?\s+is\s+required", error_message, re.IGNORECASE):
        fields.append(match.group(1))

    return [f for f in fields if f]  # filter out empties


def looks_like_placeholder_token(token: str) -> bool:
    """Simple check — is this actually a real token or just a dummy value?"""
    placeholders = {
        "token", "invalid", "invalid_token", "your_token", "xxx",
        "test", "placeholder", "<token>", "bearer", ""
    }
    cleaned = token.replace("Bearer ", "").replace("bearer ", "").strip().lower()
    return cleaned in placeholders or len(cleaned) < 10
