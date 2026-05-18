"""Browser RUM telemetry response contracts."""

from __future__ import annotations

from pydantic import BaseModel


class RumAcceptedResponse(BaseModel):
    accepted: int
    enabled: bool
