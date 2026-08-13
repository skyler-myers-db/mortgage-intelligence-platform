"""Publish the governed city dimension to the prompt guard's geography scope.

One function, its own module, because ``genie_place_dimension`` sits at 890 of
the 900-line gate and this is the seam that actually divides: RESOLVING the
governed dimension is a warehouse concern, PUBLISHING it to a schemas-side
guard vocabulary is a layering one. Split rather than allowlisted, per the
gate's own policy note.
"""

from __future__ import annotations

import logging

from backend.services.genie_place_dimension import ResolvedPlaceDimension
from backend.services.observability import emit

log = logging.getLogger(__name__)


def publish_governed_scope_cities(resolved: ResolvedPlaceDimension) -> None:
    """Hand the governed city dimension to the PROMPT guard's geography scope.

    Dependency inversion, not an import the other way: ``backend/schemas`` may
    not import ``backend/services`` (``test_schemas_do_not_import_runtime_services``),
    so the layer that owns the live dimension pushes it down. The raw
    ``all_values`` go over, not any set resolved here, because each surface
    needs its OWN admission gate -- the gate for a slot that decides whether a
    scoped criterion is reviewed is not the gate for a prose name-shape false
    positive, and the schemas side screens what it accepts.

    Publishing the DEGRADED resolve matters as much as the loaded one: an empty
    dimension withdraws the city grain, and a city-scoped question refuses
    again -- the same answer the guard gave before the scope slot existed.
    Degradation costs the grain, never the guard.

    Called under ``_load_lock``, which is safe here and would not be for most
    work in this module: the screening probe runs entirely inside
    ``backend/schemas``, and schemas cannot import services, so it has no path
    back to ``_resolve`` -- that is the same non-reentrant lock the cell probes
    deadlock on. It costs ~1.2s against the live values, and only on a load
    that actually changed them: ``register_governed_analytics_cities``
    short-circuits an unchanged dimension before screening anything.
    """

    from backend.schemas.marketing_selection_reviewed_places import (
        register_governed_analytics_cities,
    )

    try:
        admitted = register_governed_analytics_cities(resolved.all_values)
    except Exception as exc:  # noqa: BLE001 — a guard vocabulary must not 500
        emit(
            log,
            "governed_scope_cities_publish_failed",
            level=logging.WARNING,
            outcome="degraded",
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )
        return
    emit(
        log,
        "governed_scope_cities_published",
        level=logging.INFO,
        dimension_values=len(resolved.all_values),
        admitted_values=admitted,
    )
