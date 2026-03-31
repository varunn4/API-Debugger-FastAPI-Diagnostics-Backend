"""
ai_suggester.py

AI enhancement layer — improves explanations and adds suggestions.
Uses heuristic/pattern expansion for now. Can be replaced with real LLM later.
Does NOT modify or replace the rule-based logic in debugger.py.
"""

from typing import Any, Dict, List

# Toggle: set False to disable AI layer and return empty AI fields
ENABLE_AI = True


def enhance_with_ai(base_result: dict, input_data: dict) -> dict:
    """
    Enhance rule-based debug output with AI-style improvements.
    Returns only the new AI fields; caller merges with base_result.

    On failure or when ENABLE_AI=False, returns empty AI fields.
    """
    if not ENABLE_AI:
        return _empty_ai_fields()

    try:
        return _enhance(base_result, input_data)
    except Exception:
        return _empty_ai_fields()


def _empty_ai_fields() -> dict:
    """Return empty AI fields — used when disabled or on error."""
    return {
        "ai_explanation": "",
        "additional_suggestions": [],
        "ai_confidence_note": "",
    }


def _enhance(base_result: dict, input_data: dict) -> dict:
    """
    Basic heuristic for now — expands rule output into friendlier text.
    Can be replaced with real LLM call later.
    """
    root_cause = base_result.get("root_cause", "")
    suggested_fix = base_result.get("suggested_fix", "")
    issue_type = base_result.get("issue_type", "unknown")
    confidence = base_result.get("confidence_score", 0.0)
    log_flags = base_result.get("log_flags", [])

    error_message = (input_data.get("error_message") or "")
    logs = (input_data.get("logs") or "")
    payload = input_data.get("payload") or {}
    status_code = input_data.get("status_code", 0)

    # Build human-friendly explanation
    ai_explanation = _build_explanation(
        root_cause, issue_type, status_code, error_message, log_flags
    )

    # Add context-aware suggestions beyond the rule fix
    additional_suggestions = _build_additional_suggestions(
        issue_type, status_code, payload, logs, error_message, log_flags
    )

    # Note about confidence (helps user interpret the result)
    ai_confidence_note = _build_confidence_note(confidence, issue_type, log_flags)

    return {
        "ai_explanation": ai_explanation,
        "additional_suggestions": additional_suggestions,
        "ai_confidence_note": ai_confidence_note,
    }


def _build_explanation(
    root_cause: str,
    issue_type: str,
    status_code: int,
    error_message: str,
    log_flags: List[str],
) -> str:
    """Turn rule-based root_cause into a more conversational explanation."""
    parts = [f"Explanation: {root_cause}"]

    if error_message and len(error_message) < 200:
        parts.append(f"The server reported: \"{error_message}\".")

    if log_flags:
        flags_str = ", ".join(log_flags)
        parts.append(f"Log analysis detected: {flags_str}.")

    return " ".join(parts)


def _build_additional_suggestions(
    issue_type: str,
    status_code: int,
    payload: dict,
    logs: str,
    error_message: str,
    log_flags: List[str],
) -> List[str]:
    """Generate extra debugging tips based on context."""
    suggestions: List[str] = []

    # Issue-type-specific tips
    if issue_type == "auth_failure":
        suggestions.extend([
            "Ensure your token was issued for the correct environment (staging vs prod).",
            "Check if the token has the right audience (aud) claim.",
        ])
    elif issue_type == "bad_request":
        if payload:
            suggestions.append("Try validating your payload against the OpenAPI schema before sending.")
        suggestions.append("Use Postman or curl to isolate whether the issue is client-side or server-side.")
    elif issue_type == "not_found":
        suggestions.extend([
            "Verify the HTTP method (GET vs POST) — some endpoints only accept specific methods.",
            "Check for trailing slashes — /api/users and /api/users/ may differ.",
        ])
    elif issue_type == "server_error":
        suggestions.append("If reproducible, include request ID and timestamp when reporting to the API team.")
    elif issue_type == "timeout":
        suggestions.append("Consider adding request logging to measure latency and identify slow operations.")
    elif issue_type == "rate_limited":
        suggestions.append("Monitor the X-RateLimit-* headers to anticipate limits before hitting them.")

    # Log-based edge-case hints
    if "connection_refused" in log_flags:
        suggestions.append("Edge case: firewall or VPN may be blocking the connection.")
    if "db_error" in log_flags:
        suggestions.append("Edge case: retry after a few seconds — transient DB issues often resolve quickly.")

    return suggestions[:5]  # Cap at 5 to keep it concise


def _build_confidence_note(confidence: float, issue_type: str, log_flags: List[str]) -> str:
    """Help user interpret the confidence score."""
    if confidence >= 0.85:
        return "High confidence — log signals align well with the diagnosis."
    if confidence >= 0.70:
        return "Moderate confidence — likely correct, but verify against your specific setup."
    if log_flags:
        return "Lower confidence — logs provide some signal; consider gathering more context."
    return "Lower confidence — limited signal from logs; checking API docs may help."
