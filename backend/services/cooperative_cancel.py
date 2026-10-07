"""A caller-requested stop that is never a dependency failure (audit genie-03).

Stdlib only, imported by the resilience and observability layers, so it sits
below both. A :class:`CooperativeCancel` is raised by the code that OWNS the
work (the Genie completion runner's cancel points, through
``genie_completion_stages.GenieTurnCancelled``) when the work should stop
because its owner asked, not because a dependency failed. Every layer it
crosses must therefore let it through untouched:

* ``resilience.with_retry`` and ``Resilient.call`` re-raise it with no retry,
  no breaker success or failure (a half-open probe slot is returned) and no
  ``DependencyDownError`` wrap: eight parallel sweep sub-turns stopping at
  once must never open the Genie breaker for every other user;
* ``observability.timed_dependency`` logs ``outcome="cancelled"`` at INFO and
  never counts it in the ``/api/health`` error counter.

It derives from ``BaseException`` (the ``asyncio.CancelledError`` precedent)
so a broad ``except Exception`` on the governed path can never turn a stop
into a disclosed gap, a degraded answer or a rescue.
"""

from __future__ import annotations


class CooperativeCancel(BaseException):
    """A caller-requested stop; never a dependency failure."""


__all__ = ["CooperativeCancel"]
