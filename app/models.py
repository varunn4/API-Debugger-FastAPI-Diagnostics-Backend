from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DebugRequest(BaseModel):
    endpoint: str
    method: str
    headers: Optional[Dict[str, str]] = None
    payload: Optional[Dict[str, Any]] = None
    status_code: int
    error_message: Optional[str] = None
    logs: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "endpoint": "/api/v1/users",
                "method": "POST",
                "headers": {"Authorization": "Bearer invalid_token"},
                "payload": {"name": "John"},
                "status_code": 401,
                "error_message": "Unauthorized",
                "logs": "JWT expired at 10:45"
            }
        }
    }


class DebugResponse(BaseModel):
    root_cause: str
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    suggested_fix: str
    corrected_request: Dict[str, Any]
    issue_type: str
    log_flags: List[str] = []
    # AI enhancement fields (optional, populated when AI layer is enabled)
    ai_explanation: Optional[str] = None
    additional_suggestions: Optional[List[str]] = None
    ai_confidence_note: Optional[str] = None
