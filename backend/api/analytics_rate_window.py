"""Rate-window analytics endpoint: ``GET /api/v1/analytics/rate-window``.

Sibling of ``backend.api.analytics`` (kept separate so that module stays
small). Serves the "why now" series the Analytics Executive tab charts: the
weekly FRED MORTGAGE30US print against the current fixed-rate book's
note-rate band, and the in-the-money count per week. Everything is read
from ``mip.gold.rate_window_weekly``; the repository never computes a
percentile per request.

Degraded state: a cold warehouse raises ``DependencyDownError`` from the
resilient SQL client, which ``backend.main`` translates to an honest 503
``{"reason": "warming_up", "retryable": true}``. The router adds nothing
on top -- there is no fallback series.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from backend.schemas.analytics_rate_window import RateWindowResponse
from backend.services.repositories import (
    RateWindowRepository,
    get_rate_window_repository,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])

RateWindowRepoDep = Annotated[RateWindowRepository, Depends(get_rate_window_repository)]


@router.get("/rate-window", response_model=RateWindowResponse)
def rate_window(repo: RateWindowRepoDep) -> RateWindowResponse:
    """Weekly market rate against the current book, with in-the-money counts.

    The book distribution is as-of the refresh anchor (``book_as_of``) and
    applied to every historical week; the provenance block says so and
    names every table the series was read from.
    """
    return repo.rate_window()
