"""
tests/test_api.py

Realistic test cases for the debug endpoint.
Not trying to hit 100% coverage of every line —
just the scenarios that actually matter in practice.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.log_parser import parse_logs
from app.validator import extract_missing_fields, looks_like_placeholder_token, validate
from app.models import DebugRequest

client = TestClient(app)


# ── helper ─────────────────────────────────────────────────────────────────
def post_debug(payload):
    return client.post("/debug", json=payload)


# ── basic smoke ────────────────────────────────────────────────────────────
def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── 401 cases ─────────────────────────────────────────────────────────────
def test_expired_token():
    r = post_debug({
        "endpoint": "/api/v1/users",
        "method": "GET",
        "headers": {"Authorization": "Bearer some.jwt.token"},
        "status_code": 401,
        "error_message": "Unauthorized",
        "logs": "JWT expired at 2024-01-01 10:45:00",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["issue_type"] == "auth_failure"
    assert data["confidence_score"] >= 0.85
    assert "expired" in data["root_cause"].lower() or "token" in data["root_cause"].lower()
    assert "log_flags" in data
    assert "expired_token" in data["log_flags"]


def test_placeholder_token():
    r = post_debug({
        "endpoint": "/api/v1/profile",
        "method": "GET",
        "headers": {"Authorization": "Bearer token"},
        "status_code": 401,
        "error_message": "Unauthorized",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["issue_type"] == "auth_failure"
    assert data["confidence_score"] >= 0.90
    assert "placeholder" in data["root_cause"].lower()


# ── 400 cases ─────────────────────────────────────────────────────────────
def test_missing_field():
    r = post_debug({
        "endpoint": "/api/v1/users",
        "method": "POST",
        "headers": {"Authorization": "Bearer valid_token"},
        "payload": {"name": "John"},
        "status_code": 400,
        "error_message": "Missing required field: email",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["issue_type"] == "bad_request"
    assert data["confidence_score"] >= 0.80
    # should have patched the corrected request with the missing field
    assert "email" in data["corrected_request"]["payload"]


def test_invalid_json_in_logs():
    r = post_debug({
        "endpoint": "/api/v1/data",
        "method": "POST",
        "headers": {"Authorization": "Bearer valid_token"},
        "payload": {"key": "value"},
        "status_code": 400,
        "error_message": "Bad Request",
        "logs": "invalid json: unexpected token at position 42",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["issue_type"] == "bad_request"
    assert "invalid_json" in data["log_flags"]


# ── 403 ───────────────────────────────────────────────────────────────────
def test_permission_denied():
    r = post_debug({
        "endpoint": "/api/v1/admin/users",
        "method": "DELETE",
        "headers": {"Authorization": "Bearer regular_user_token_12345"},
        "status_code": 403,
        "error_message": "Forbidden",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["issue_type"] == "permission_denied"
    assert "permission" in data["root_cause"].lower() or "role" in data["root_cause"].lower()


# ── 404 ───────────────────────────────────────────────────────────────────
def test_wrong_endpoint_no_version():
    r = post_debug({
        "endpoint": "/users/profile",
        "method": "GET",
        "headers": {"Authorization": "Bearer valid_token_12345"},
        "status_code": 404,
        "error_message": "Not Found",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["issue_type"] == "not_found"
    # should suggest adding version prefix
    assert "/api/v1" in data["corrected_request"]["endpoint"]


def test_versioned_endpoint_404():
    r = post_debug({
        "endpoint": "/api/v1/usr",  # typo
        "method": "GET",
        "headers": {"Authorization": "Bearer valid_token_12345"},
        "status_code": 404,
        "error_message": "Not Found",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["issue_type"] == "not_found"
    assert data["confidence_score"] >= 0.75


# ── timeout ───────────────────────────────────────────────────────────────
def test_timeout_via_logs():
    r = post_debug({
        "endpoint": "/api/v1/reports",
        "method": "POST",
        "headers": {"Authorization": "Bearer valid_token_12345"},
        "payload": {"from": "2023-01-01", "to": "2023-12-31"},
        "status_code": 504,
        "error_message": "Gateway Timeout",
        "logs": "upstream timed out (110: Connection timed out) while reading response header",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["issue_type"] == "timeout"
    assert "timeout" in data["log_flags"]
    assert data["confidence_score"] >= 0.75


def test_timeout_detected_without_504():
    """timeout in logs should override a generic 500"""
    r = post_debug({
        "endpoint": "/api/v1/process",
        "method": "POST",
        "headers": {"Authorization": "Bearer tok_12345"},
        "status_code": 500,
        "error_message": "Internal Server Error",
        "logs": "read timed out after 30s",
    })
    assert r.status_code == 200
    data = r.json()
    # timeout rule runs before server_error
    assert data["issue_type"] == "timeout"


# ── 500 ───────────────────────────────────────────────────────────────────
def test_server_error_with_db_flag():
    r = post_debug({
        "endpoint": "/api/v1/orders",
        "method": "POST",
        "headers": {"Authorization": "Bearer valid_tok_xyz"},
        "payload": {"item": "widget", "qty": 2},
        "status_code": 500,
        "error_message": "Internal Server Error",
        "logs": "OperationalError: database connection pool exhausted",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["issue_type"] == "server_error"
    assert "db_error" in data["log_flags"]
    assert data["confidence_score"] >= 0.78


# ── edge cases ────────────────────────────────────────────────────────────
def test_conflicting_signals_401_and_timeout():
    """
    401 status + timeout in logs. Timeout rule has higher priority (runs first),
    but the status code is 401 so auth rule should win since timeout
    is only triggered by 504 / timeout keyword in error.
    Let's verify whichever rule fires, confidence is reasonable.
    """
    r = post_debug({
        "endpoint": "/api/v1/users",
        "method": "GET",
        "headers": {"Authorization": "Bearer tok_123456"},
        "status_code": 401,
        "error_message": "Unauthorized",
        "logs": "connection timed out — but also JWT expired",
    })
    assert r.status_code == 200
    data = r.json()
    # both signals present — timeout rule fires first because logs have "timed out"
    # this is fine, the test just ensures we don't crash or return garbage
    assert data["issue_type"] in ("timeout", "auth_failure")
    assert 0.5 <= data["confidence_score"] <= 1.0


def test_empty_optional_fields():
    """Minimum viable request — just endpoint, method, status_code."""
    r = post_debug({
        "endpoint": "/api/v1/ping",
        "method": "GET",
        "status_code": 404,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["issue_type"] == "not_found"


def test_rate_limited():
    r = post_debug({
        "endpoint": "/api/v1/search",
        "method": "GET",
        "headers": {"Authorization": "Bearer valid_tok_abc"},
        "status_code": 429,
        "error_message": "Too Many Requests",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["issue_type"] == "rate_limited"
    assert data["confidence_score"] >= 0.85


def test_invalid_endpoint_rejected():
    r = post_debug({
        "endpoint": "api/v1/no-leading-slash",
        "method": "GET",
        "status_code": 200,
    })
    assert r.status_code == 422


def test_invalid_status_code():
    r = post_debug({
        "endpoint": "/api/v1/test",
        "method": "GET",
        "status_code": 99,  # below valid range
    })
    # pydantic will catch this at the model level
    assert r.status_code == 422


# ── unit tests for sub-modules ────────────────────────────────────────────
class TestLogParser:
    def test_detects_expired_token(self):
        flags = parse_logs("JWT token expired at 12:00")
        assert "expired_token" in flags

    def test_detects_timeout(self):
        flags = parse_logs("upstream timed out after 30s")
        assert "timeout" in flags

    def test_detects_connection_refused(self):
        flags = parse_logs("Error: ECONNREFUSED 127.0.0.1:5432")
        assert "connection_refused" in flags

    def test_empty_log(self):
        assert parse_logs("") == []
        assert parse_logs(None) == []

    def test_multiple_flags(self):
        flags = parse_logs("connection refused\nJWT expired\nmissing required field email")
        assert "connection_refused" in flags
        assert "expired_token" in flags
        assert "missing_field" in flags


class TestValidator:
    def test_extract_missing_single(self):
        fields = extract_missing_fields("Missing required field: email")
        assert "email" in fields

    def test_extract_missing_multiple(self):
        fields = extract_missing_fields("Missing required fields: email, phone, address")
        assert "email" in fields
        assert "phone" in fields

    def test_placeholder_detection(self):
        assert looks_like_placeholder_token("Bearer token") is True
        assert looks_like_placeholder_token("Bearer invalid") is True
        assert looks_like_placeholder_token("Bearer eyJhbGciOiJIUzI1NiJ9.realtoken") is False

    def test_validate_bad_endpoint(self):
        req = DebugRequest(endpoint="no-slash", method="GET", status_code=200)
        ok, problems = validate(req)
        assert not ok
        assert any("/" in p for p in problems)

    def test_validate_good_request(self):
        req = DebugRequest(endpoint="/api/v1/test", method="POST", status_code=400)
        ok, _ = validate(req)
        assert ok
