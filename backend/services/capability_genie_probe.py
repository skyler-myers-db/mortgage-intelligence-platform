"""Live Genie capability probes (Conversation API + native visualization).

Split out of ``backend.services.capabilities`` to keep that module under the
file-size gate. Both probes are fed by a SINGLE live Genie turn so the space
rate limit (~5/min) is respected -- ``collect_live_capability_statuses`` runs
``probe_genie_turn`` once and passes the response into
``probe_native_visualization``.

``make_status`` is injected (the same seam ``ai_gateway_capability_probe`` uses)
so this module never imports ``LiveCapabilityStatus`` from ``capabilities`` --
that would be a circular import.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class _StatusFactory(Protocol):
    def __call__(self, available: bool, detail: str) -> Any: ...


def probe_genie_turn(
    genie_client: Any,
    *,
    make_status: _StatusFactory,
) -> tuple[Any, Any | None]:
    """Run ONE live Genie turn; return its Conversation-API status + response.

    The response is returned so the native-visualization row can be derived
    from the same turn without issuing a second (rate-limited) question.
    """
    try:
        ask: Callable[..., Any] | None = getattr(genie_client, "ask", None)
        if not callable(ask):
            return (
                make_status(
                    False,
                    "Genie client does not expose a Conversation API turn probe.",
                ),
                None,
            )
        response = ask(
            "Capability readiness check: reply with one short sentence about the Mortgage Lead Intelligence trusted assets."
        )
    except Exception as exc:  # noqa: BLE001 - probe must not fail the surface
        return (
            make_status(False, f"Genie Conversation API turn raised {type(exc).__name__}."),
            None,
        )
    conversation_id = str(getattr(response, "conversation_id", "") or "").strip()
    message_id = str(getattr(response, "message_id", "") or "").strip()
    if conversation_id and message_id:
        return (
            make_status(
                True,
                "Live Genie Conversation API turn completed for this workspace.",
            ),
            response,
        )
    return (
        make_status(False, "Genie turn did not return a conversation and message id."),
        response,
    )


def probe_native_visualization(
    genie_client: Any,
    response: Any | None,
    *,
    make_status: _StatusFactory,
) -> Any:
    """Derive the native-visualization live status from the probe turn.

    Honest, bounded, no extra Genie question:

    * No response / no viz attachment on the turn -> not available (the space
      did not return a native visualization for the probe question).
    * Viz attachment present but the Beta download endpoint does not return
      200 with content -> not available (documented 404 on this workspace).
    * Viz attachment present AND the download returns 200 with content ->
      available (claimable).
    """
    if response is None:
        return make_status(
            False,
            "Genie turn did not complete, so no native visualization was returned.",
        )
    native = getattr(response, "native_visualization", None)
    if not isinstance(native, dict) or not native.get("attachment_id"):
        return make_status(
            False,
            "Genie turn did not return a native-visualization attachment.",
        )
    conversation_id = str(getattr(response, "conversation_id", "") or "").strip()
    message_id = str(getattr(response, "message_id", "") or "").strip()
    attachment_id = str(native.get("attachment_id") or "").strip()
    download = getattr(genie_client, "download_native_visualization", None)
    if not (conversation_id and message_id and attachment_id and callable(download)):
        return make_status(
            False,
            "Genie returned a native visualization attachment, but the Beta download "
            "endpoint is not yet available in this workspace.",
        )
    try:
        downloaded = download(conversation_id, message_id, attachment_id)
    except Exception as exc:  # noqa: BLE001 - probe must not fail the surface
        return make_status(
            False,
            f"Native-visualization download probe raised {type(exc).__name__}.",
        )
    if downloaded:
        return make_status(
            True,
            "Live Genie native-visualization attachment downloaded for this workspace.",
        )
    return make_status(
        False,
        "Genie returned a native visualization attachment, but the Beta download "
        "endpoint is not yet available in this workspace.",
    )
