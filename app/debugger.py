"""
debugger.py

Rule-based engine that looks at the status code, error message, and log flags
and returns a diagnosis + corrected request.

I'm using plain if/elif here instead of a fancy registry pattern because:
1. There are only ~6 cases to handle
2. It's easier to read and explain in an interview
3. Adding new rules is just adding an elif — no magic required

Confidence scoring: base value per error type, boosted if the log
signals agree with the status code.
"""

import copy
from typing import Any, Dict, List

from app.models import DebugRequest, DebugResponse
from app.validator import extract_missing_fields, looks_like_placeholder_token


def debug(req: DebugRequest, log_flags: List[str]) -> DebugResponse:
    error_msg = (req.error_message or "").lower()
    status = req.status_code
    headers = req.headers or {}
    payload = req.payload or {}
    auth_header = headers.get("Authorization", headers.get("authorization", ""))

    # we'll mutate this to produce the corrected request
    corrected = {
        "endpoint": req.endpoint,
        "method": req.method.upper(),
        "headers": copy.deepcopy(headers),
        "payload": copy.deepcopy(payload),
    }

    # --- timeout (check logs first — timeout can show up with various status codes)
    if "timeout" in log_flags or "timeout" in error_msg or status == 504:
        return _timeout_result(req, corrected, log_flags)

    # --- auth issues
    if status == 401 or "unauthorized" in error_msg or "expired_token" in log_flags:
        return _auth_result(req, corrected, auth_header, log_flags)

    # --- permission issues
    if status == 403 or "forbidden" in error_msg or "permission_denied" in log_flags:
        return _permission_result(req, corrected, log_flags)

    # --- not found
    if status == 404 or "not_found" in log_flags or "not found" in error_msg:
        return _not_found_result(req, corrected, log_flags)

    # --- bad request — could be missing fields or bad JSON
    if status == 400:
        return _bad_request_result(req, corrected, error_msg, log_flags)

    # --- server error
    if 500 <= status <= 599:
        return _server_error_result(req, corrected, log_flags)

    # --- rate limited
    if status == 429 or "rate_limited" in log_flags:
        return _rate_limit_result(req, corrected, log_flags)

    # fallback — we don't have a specific rule for this
    return DebugResponse(
        root_cause=f"Unrecognized failure pattern (status {status}). No matching rule found.",
        confidence_score=0.40,
        suggested_fix="Check the API documentation for status code " + str(status) + " and review the error message.",
        corrected_request=corrected,
        issue_type="unknown",
        log_flags=log_flags,
    )


# ── individual rule handlers ──────────────────────────────────────────────────

def _auth_result(req, corrected, auth_header, log_flags):
    is_expired = "expired_token" in log_flags
    is_placeholder = looks_like_placeholder_token(auth_header)

    if is_placeholder:
        cause = "The Authorization header contains a placeholder/dummy token, not a real one."
        fix = "Replace the token with a valid one. Get a fresh token from your auth endpoint (e.g. POST /auth/login) and set it as 'Bearer <real_token>'."
        confidence = 0.92
    elif is_expired:
        cause = "The JWT/session token has expired. The server rejected the request because the token is no longer valid."
        fix = "Refresh the token using your refresh token endpoint before retrying. Most auth systems have a POST /auth/refresh endpoint."
        confidence = 0.90
    else:
        cause = "Authentication failed (401). Token may be malformed, signed with wrong secret, or for a different environment (staging vs prod)."
        fix = "Verify the token is correct for this environment. Try decoding it at jwt.io to check the claims and expiry."
        confidence = 0.72

    corrected["headers"]["Authorization"] = "Bearer <valid_token>"

    return DebugResponse(
        root_cause=cause,
        confidence_score=confidence,
        suggested_fix=fix,
        corrected_request=corrected,
        issue_type="auth_failure",
        log_flags=log_flags,
    )


def _permission_result(req, corrected, log_flags):
    return DebugResponse(
        root_cause="Authenticated user doesn't have permission to perform this action (403 Forbidden). Valid token, wrong role/scope.",
        confidence_score=0.85,
        suggested_fix="Check that the user account has the required role or scope for this endpoint. If using RBAC, the user may need 'admin' or 'write' permissions.",
        corrected_request=corrected,
        issue_type="permission_denied",
        log_flags=log_flags,
    )


def _not_found_result(req, corrected, log_flags):
    endpoint = req.endpoint

    # quick heuristic: does the endpoint look versioned?
    # can be improved later with actual route introspection
    has_version = "/v1/" in endpoint or "/v2/" in endpoint or "/v3/" in endpoint

    if not has_version:
        fix = f"The endpoint '{endpoint}' was not found. Check the API docs — the path might need a version prefix like /api/v1{endpoint}."
        corrected["endpoint"] = f"/api/v1{endpoint}"
        confidence = 0.68
    else:
        fix = f"Endpoint '{endpoint}' doesn't exist. Double-check the path spelling and HTTP method. Some APIs are case-sensitive."
        confidence = 0.78

    return DebugResponse(
        root_cause=f"404 Not Found — the server has no route matching '{endpoint}'.",
        confidence_score=confidence,
        suggested_fix=fix,
        corrected_request=corrected,
        issue_type="not_found",
        log_flags=log_flags,
    )


def _bad_request_result(req, corrected, error_msg, log_flags):
    payload = req.payload or {}

    # try to figure out which fields are missing
    missing = extract_missing_fields(req.error_message or "")

    if "invalid_json" in log_flags or "invalid json" in error_msg:
        cause = "The request body contains malformed JSON that the server couldn't parse."
        fix = "Validate your JSON before sending — run it through jsonlint.com or use json.dumps() to serialize it properly. Make sure Content-Type is 'application/json'."
        confidence = 0.88
        corrected["headers"]["Content-Type"] = "application/json"

    elif missing:
        field_list = ", ".join(missing)
        cause = f"Request is missing required field(s): {field_list}."
        fix = f"Add the missing field(s) to the request body: {field_list}. Check the API schema for their expected types."

        # patch the corrected payload with placeholder values
        for field in missing:
            if field not in corrected["payload"]:
                corrected["payload"][field] = f"<{field}_value>"

        # more confident if we could extract the exact field names
        confidence = 0.87

    elif "missing_field" in log_flags:
        cause = "One or more required fields are missing from the request body."
        fix = "Check the API documentation for required fields and add them to the payload."
        confidence = 0.75

    else:
        cause = "Bad request (400) — the server rejected the input. Could be a type mismatch, invalid enum value, or constraint violation."
        fix = "Review the API schema carefully. Check field types (string vs int), enums, min/max values, and format constraints (e.g. email format, date format)."
        confidence = 0.60

    return DebugResponse(
        root_cause=cause,
        confidence_score=confidence,
        suggested_fix=fix,
        corrected_request=corrected,
        issue_type="bad_request",
        log_flags=log_flags,
    )


def _server_error_result(req, corrected, log_flags):
    status = req.status_code
    extra = ""

    if "db_error" in log_flags:
        cause = "Server crashed with a database error. The request itself may be fine — this is a backend issue."
        fix = "This is a server-side bug. Report it to the API team with your request details and timestamp. If you own the server: check DB connection pool, query timeouts, and disk space."
        confidence = 0.82
    elif "null_pointer" in log_flags:
        cause = "Server threw a NullPointerException / AttributeError, likely because your input triggered an unhandled edge case."
        fix = "Check if any optional fields you're sending (or not sending) are expected to always be present. Try sending a more complete payload."
        confidence = 0.70
    else:
        cause = f"{status} Internal Server Error — the server crashed while processing your request."
        fix = "Check server logs for the stack trace. Common causes: unhandled edge case in input, DB failure, downstream service down."
        confidence = 0.55

    return DebugResponse(
        root_cause=cause,
        confidence_score=confidence,
        suggested_fix=fix,
        corrected_request=corrected,
        issue_type="server_error",
        log_flags=log_flags,
    )


def _timeout_result(req, corrected, log_flags):
    return DebugResponse(
        root_cause="The request timed out. Either the server took too long to respond or the network connection was interrupted.",
        confidence_score=0.80,
        suggested_fix="1. Retry the request — could be a transient issue. 2. If consistent: check if the endpoint does heavy processing (consider async jobs). 3. Increase client timeout. 4. Check network/firewall rules between client and server.",
        corrected_request=corrected,
        issue_type="timeout",
        log_flags=log_flags,
    )


def _rate_limit_result(req, corrected, log_flags):
    return DebugResponse(
        root_cause="Too many requests — you've hit the API rate limit (429).",
        confidence_score=0.90,
        suggested_fix="Back off and retry. Check the Retry-After header in the response. Implement exponential backoff in your client. Consider caching responses to reduce request volume.",
        corrected_request=corrected,
        issue_type="rate_limited",
        log_flags=log_flags,
    )
