"""Shared HTTP content negotiation helpers."""

from __future__ import annotations

from fastapi import HTTPException, Request

JSON_CONTENT_TYPE_RESPONSE = {
    415: {
        "description": "Unsupported content type",
        "content": {
            "application/json": {
                "example": {"detail": "Unsupported content type"},
            }
        },
    }
}


def require_json_content_type(request: Request) -> None:
    """Require JSON request bodies for endpoints that bind structured data."""

    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Unsupported content type")
