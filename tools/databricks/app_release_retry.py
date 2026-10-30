"""Bounded retry of an App release workspace call on HTTP 429, and on nothing else.

2026-10-29: ``app_release_source publish`` died part way through a 1,177-file
release on ``ResourceExhausted: Too many requests`` raised by
``workspace.upload``. SDK 0.103.0's ``ApiClient.do`` retries a throttled
request with no body or a rewindable one, until its own retry timeout, but
``workspace.upload`` passes its form fields as a dict, which ``do`` sends
once. So an upload 429 always reached the caller. This module is that missing
retry.

The release tools' download and listing calls are GETs, which ``do`` retries
itself: every 429 (and 503) carries ``retry_after_secs``, so the SDK waits and
retries it until ``retry_timeout_seconds`` (300 s unless configured) and then
raises ``TimeoutError`` from the last 429 (``retries.retried`` raises
``RuntimeError`` instead when a ``max_attempts`` runs out first; ``do`` sets
none). No 429 reaches the caller from a GET. ``call``
refuses that shape at once as RateLimitExhausted, naming the call and the
SDK's timeout, without a second budget on top of the 300 s already spent; a
timeout from anything else (a 503, a dropped connection) propagates
unchanged.

``RateLimitRetry.call`` gives one call ``attempts`` tries against a 429 that
does reach it. The wait after try n is a draw from the upper half of
``base * 2**(n-1)`` (``jitter`` is 0..1), raised to the error's Retry-After
(the SDK's ``retry_after_secs``) and cut to ``cap``, so a longer Retry-After
waits ``cap`` and the budget stays bounded. Each retry prints one
``[app-release]`` line to stderr, so a deploy log shows how often it was
throttled. Any other error propagates on its first occurrence. A call still
throttled on its last try raises RateLimitExhausted, a ReleaseContractError,
so the release CLIs refuse it with exit 2. ``sleep`` and ``jitter`` are
injectable so tests never wait.
"""

from __future__ import annotations

import random
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from databricks.sdk.errors import RequestLimitExceeded, ResourceExhausted, TooManyRequests
from tools.databricks.app_release_contract import ReleaseContractError

_R = TypeVar("_R")

# Every SDK 0.103.0 class that means HTTP 429: the status maps to
# TooManyRequests, the RESOURCE_EXHAUSTED and REQUEST_LIMIT_EXCEEDED codes to
# its two subclasses. Named one by one so a re-parented class still retries.
RATE_LIMIT_ERRORS: tuple[type[Exception], ...] = (
    TooManyRequests, ResourceExhausted, RequestLimitExceeded,
)


class RateLimitExhausted(ReleaseContractError):
    """Every attempt of one call was rate limited; nothing was overwritten."""


def _sdk_retry_gave_up_on_a_rate_limit(error: BaseException) -> bool:
    """``ApiClient.do``'s own retry ran out while the request was still a 429."""
    return (
        isinstance(error, TimeoutError | RuntimeError)
        and not isinstance(error, ReleaseContractError)
        and isinstance(error.__cause__, RATE_LIMIT_ERRORS)
    )


@dataclass(frozen=True)
class RateLimitRetry:
    """Exponential backoff with jitter for HTTP 429 alone, within a fixed budget."""

    attempts: int = 6
    base_seconds: float = 2.0
    cap_seconds: float = 30.0
    sleep: Callable[[float], None] = time.sleep
    jitter: Callable[[], float] = random.random

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("RateLimitRetry needs at least one attempt")

    def delay(self, attempt: int, error: BaseException) -> float:
        """The wait after failed try ``attempt`` (1-based), Retry-After included."""
        backoff = min(self.cap_seconds, self.base_seconds * 2 ** (attempt - 1))
        wait = backoff * (1 + self.jitter()) / 2
        hint = getattr(error, "retry_after_secs", None)
        if isinstance(hint, int | float) and not isinstance(hint, bool) and hint > 0:
            wait = max(wait, float(hint))
        return min(self.cap_seconds, wait)

    def call(self, what: str, function: Callable[[], _R]) -> _R:
        """``function()``, retried on a 429; refuses once this budget or the SDK's is spent."""
        for attempt in range(1, self.attempts + 1):
            try:
                return function()
            except RATE_LIMIT_ERRORS as error:
                if attempt == self.attempts:
                    raise RateLimitExhausted(
                        f"the {what} was rate limited (HTTP 429) on all {self.attempts} "
                        f"attempts of its retry budget (at most {self.cap_seconds:g} s per "
                        "wait); nothing was overwritten or deleted, so a re-run is safe"
                    ) from error
                wait = self.delay(attempt, error)
                print(
                    f"[app-release] the {what} was rate limited (HTTP 429); try "
                    f"{attempt + 1} of {self.attempts} in {wait:.1f} s",
                    file=sys.stderr, flush=True,
                )
                self.sleep(wait)
            except (TimeoutError, RuntimeError) as error:
                if not _sdk_retry_gave_up_on_a_rate_limit(error):
                    raise
                raise RateLimitExhausted(
                    f"the {what} was still rate limited (HTTP 429) when the SDK's own retry "
                    f"gave up ({error}); nothing was overwritten or deleted, so a re-run is safe"
                ) from error
        raise AssertionError("unreachable: attempts is at least 1")


RATE_LIMIT_RETRY = RateLimitRetry()
