"""Pre-stop probe: one real Genie turn on the governed space.

The App's Genie answers, the live smoke's Genie message probe and the agent
evaluation all need the workspace's Genie model budget. When that budget is
spent, Genie turns fail with a budget error while the health ping, which
issues no turn, still reports Genie up; the run then fails at smoke or
evaluation, after step 0a has stopped the App. This probe issues exactly one
turn first, through the App's own live client (``ASSERT`` is
``GenieClient.ask``, the call the App makes for every Genie question, which
the provisioner's live smoke also makes), on the governed space, while the App
still serves. A failed or unfinished turn, or one that returns only the
question's echo, refuses; a budget error is named as such. The governed space
is the signed record's, or without a record the one the provisioner resolves
by its pin, never by its title alone (``prestop_probe_suite.governed_genie_space``).

The turn runs as the deployer, so it proves the space and the workspace budget,
not the App's own grant (the post-stop audits prove that). It is the probe's
only call that is not a GET or a list: it opens one Genie conversation. With no
governed space yet (a first install) there is no space to ask, so it is
skipped. Tests always stub it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.services.genie_client import GenieClient
from tools.databricks.prestop_refusals import GENIE_BUDGET, PrestopRefusal

# The App's live Genie call (identity, not a copy).
ASSERT = GenieClient.ask
# The live smoke's Genie question (scripts/smoke_live.sh).
QUESTION = "How many borrowers are currently in-the-money?"
TURN_TIMEOUT_S = 90
_BUDGET_MARKERS = ("budget", "quota", "resource_exhausted", "http 429")


def deployer_token_provider(workspace: Any) -> Callable[[], str]:
    """The deployer's bearer for each request, as the SDK's own auth resolves it."""

    def token() -> str:
        header = str(workspace.config.authenticate().get("Authorization", ""))
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            raise RuntimeError("the deployer's workspace auth produced no bearer token")
        return value.strip()

    return token


def genie_budget_refusals(
    *,
    host: str,
    token_provider: Callable[[], str],
    space_id: str,
    client_factory: Callable[..., Any] = GenieClient,
) -> tuple[list[PrestopRefusal], list[str]]:
    """Ask the governed space one question; refuse unless the turn completes."""

    if not space_id.strip():
        return [], ["no governed Genie space exists yet; the Genie budget turn is skipped"]
    client = client_factory(host, token_provider, space_id.strip(), timeout_s=TURN_TIMEOUT_S)
    try:
        response = ASSERT(client, QUESTION)
    except Exception as exc:  # noqa: BLE001 - any failed turn refuses
        message = str(exc)
        reason = (
            "the workspace Genie budget is exhausted"
            if any(marker in message.lower() for marker in _BUDGET_MARKERS)
            else "the turn did not complete"
        )
        return [
            PrestopRefusal(
                GENIE_BUDGET,
                f"one Genie turn on the governed space {space_id} failed ({reason}): "
                f"{type(exc).__name__}: {message[:300]}. Smoke and agent evaluation need "
                "Genie after step 0a stops the App. Wait for the budget to reset or have the "
                "workspace admin raise it, then re-run.",
            )
        ], []
    status = str(getattr(response, "genie_status", "") or "COMPLETED")
    answer = str(getattr(response, "answer_text", "") or "").strip()
    outcome = ""
    if status != "COMPLETED":
        outcome = f"ended {status}, not COMPLETED"
    elif not answer or answer.casefold() == QUESTION.casefold():
        # The provisioner's live smoke rejects the same echo (provision_genie_space).
        outcome = "completed with no answer beyond the prompt echo"
    if outcome:
        return [
            PrestopRefusal(
                GENIE_BUDGET,
                f"one Genie turn on the governed space {space_id} {outcome}. Smoke and agent "
                "evaluation need Genie after step 0a stops the App. Then re-run.",
            )
        ], []
    return [], [f"one Genie turn on the governed space {space_id} completed"]
