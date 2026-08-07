"""Deterministic, server-authored process trace for one governed Genie turn.

Why this module exists
----------------------
``genie_reasoning_trace_from_thoughts`` translates the model's *private*
thoughts into a tiny fixed vocabulary, which is correct for safety (raw
thoughts must never ship) but useless as an explanation: a normal turn
produced four identical "Analyzed the request within the governed Genie
workflow." lines. Proof UI that repeats itself teaches nothing and reads as
filler.

This builder records what the *governed pipeline itself* did on the turn --
guardrails, live drafting, trust policy, narrative repair, canonical
re-execution, row execution, narrative verification, brief composition -- as
ordered steps with distinct kinds. Every string is authored here, in server
code, from structural facts (asset names already extracted by the trust
policy, row counts, which branch executed). No model text is ever echoed.

Safety contract
---------------
Each emitted ``content`` is scanned with ``genie_visible_text_unsafe`` before
it enters the trace, exactly like every other model-adjacent visible string.
Asset names are the only interpolated values that originate downstream of the
model (via ``_extract_asset_refs``), so a step whose asset-bearing wording
fails the scan falls back to asset-free wording and is dropped entirely if
that fails too. Fail-closed, never redact-in-place.
"""

from __future__ import annotations

from backend.services.genie_answers import GenieReasoningStep
from backend.services.genie_message_policy import genie_visible_text_unsafe

# Same cap as the thought translation path: a bounded proof body.
GENIE_MAX_TRACE_STEPS = 12

# Number of asset names named inline before the wording switches to a summary
# phrase. Two keeps the sentence readable for the common
# lead_population + borrower_360 pair.
_MAX_NAMED_ASSETS = 2

_GENERIC_ASSET_PHRASE = "the trusted gold assets"

# Translated live-thought contents that the pipeline steps already state more
# precisely. When a translated thought reduces to one of these, it adds nothing
# beyond the deterministic trace and is dropped rather than appended.
SUPERSEDED_TRANSLATED_CONTENTS: frozenset[str] = frozenset(
    {
        # The generic catch-all: it describes nothing.
        "Analyzed the request within the governed Genie workflow.",
        # Superseded by the richer `live` + `trust` + `execute` steps.
        "Prepared a governed query plan over approved data assets.",
    }
)


def _asset_phrase(assets: list[str] | None) -> str:
    named = [str(asset).strip() for asset in (assets or []) if str(asset).strip()]
    if not named:
        return _GENERIC_ASSET_PHRASE
    return ", ".join(named[:_MAX_NAMED_ASSETS])


class GenieProcessTrace:
    """Ordered, deduped, server-authored steps describing one governed turn.

    The repository populates this as the turn progresses; ``steps()`` renders
    the wire models for both ``proof.reasoning_trace`` and the response-level
    ``reasoning_trace``.
    """

    __slots__ = ("_steps",)

    def __init__(self) -> None:
        self._steps: list[tuple[str, str]] = []

    # -- internals ---------------------------------------------------

    def _add(self, kind: str, content: str, *, fallback: str | None = None) -> None:
        for candidate in (content, fallback):
            if not candidate:
                continue
            if genie_visible_text_unsafe(candidate):
                continue
            if any(existing == candidate for _, existing in self._steps):
                return
            if len(self._steps) >= GENIE_MAX_TRACE_STEPS:
                return
            self._steps.append((kind, candidate))
            return

    # -- pipeline steps ----------------------------------------------

    def guardrails(self) -> None:
        """Every turn reaching the repository cleared the router guard battery."""
        self._add(
            "guardrails",
            "Prompt cleared the governed guardrails: fair-lending, PII, scope, "
            "and injection screens.",
        )

    def repair(self) -> None:
        """A data question came back narrative-only and was regenerated."""
        self._add(
            "repair",
            "Narrative-only turn repaired: asked Genie to regenerate the answer "
            "as governed SQL.",
        )

    def live_turn(self, *, sql_query: str | None, assets: list[str] | None) -> None:
        if not sql_query:
            self._add(
                "live",
                "The live Genie turn returned a narrative draft with no SQL attachment.",
            )
            return
        self._add(
            "live",
            f"The live Genie turn drafted a SQL plan over {_asset_phrase(assets)}.",
            fallback=(
                f"The live Genie turn drafted a SQL plan over {_GENERIC_ASSET_PHRASE}."
            ),
        )

    def trust(self) -> None:
        self._add(
            "trust",
            "Generated SQL passed the trust policy: a single read-only SELECT "
            "over trusted assets.",
        )

    def canonical(self, *, shape: str) -> None:
        """A recognized grain-sensitive shape was re-executed canonically.

        ``shape`` is a server-chosen label ("ranking" or "metric"), never model
        text; unknown labels degrade to the neutral wording.
        """
        if shape == "metric":
            self._add(
                "canonical",
                "Recognized metric shape: re-executed the governed canonical "
                "count at the unique-borrower grain.",
            )
            return
        if shape == "ranking":
            self._add(
                "canonical",
                "Recognized ranking shape: re-executed the governed canonical "
                "query at the unique-borrower grain.",
            )
            return
        self._add(
            "canonical",
            "Recognized a governed shape: re-executed the canonical query at "
            "the unique-borrower grain.",
        )

    def execute(self, *, row_count: int, assets: list[str] | None) -> None:
        count = max(int(row_count), 0)
        row_word = "row" if count == 1 else "rows"
        self._add(
            "execute",
            f"Returned {count:,} {row_word} from {_asset_phrase(assets)}; "
            "PII columns stripped at the boundary.",
            fallback=(
                f"Returned {count:,} {row_word} from {_GENERIC_ASSET_PHRASE}; "
                "PII columns stripped at the boundary."
            ),
        )

    def verified(self) -> None:
        self._add("verify", "Narrative numbers verified against the returned rows.")

    def narrative_withheld(self, *, reason: str) -> None:
        """Record the ACTUAL reason the model prose did not render.

        ``reason`` is one of this module's server-owned enum values; anything
        else degrades to the neutral withholding wording rather than echoing a
        caller-supplied string.
        """
        self._add("verify", NARRATIVE_WITHHELD_CONTENT.get(reason, _WITHHELD_DEFAULT))

    def composed_brief(self) -> None:
        self._add(
            "compose",
            "Composed the per-candidate analyst brief from governed row values.",
        )

    # -- rendering ----------------------------------------------------

    def steps(
        self,
        live_steps: list[GenieReasoningStep] | None = None,
    ) -> list[GenieReasoningStep]:
        """Render the trace, optionally appending non-generic live thoughts.

        Translated live thoughts are appended only when they say something the
        deterministic pipeline steps do not already say (see
        ``SUPERSEDED_TRANSLATED_CONTENTS``) and are not duplicates.
        """
        rendered = [GenieReasoningStep(kind=kind, content=content) for kind, content in self._steps]
        seen = {content for _, content in self._steps}
        for step in live_steps or []:
            if len(rendered) >= GENIE_MAX_TRACE_STEPS:
                break
            content = (step.content or "").strip()
            if not content or content in seen or content in SUPERSEDED_TRANSLATED_CONTENTS:
                continue
            if genie_visible_text_unsafe(content):
                continue
            seen.add(content)
            rendered.append(GenieReasoningStep(kind=step.kind, content=content))
        return rendered


# Reasons the model narrative did not render. Keys are internal; values are the
# only strings that ship.
WITHHELD_UNSAFE_TEXT = "unsafe_text"
WITHHELD_UNVERIFIED_NUMBERS = "unverified_numbers"
WITHHELD_CONTRADICTED = "contradicted"
#: The live turn produced no prose and there is no deterministic summary to
#: stand in for it -- the rows and generated SQL are the whole answer.
WITHHELD_NO_NARRATIVE = "no_narrative"
#: The live turn produced no prose, but a governed canonical answer did.
WITHHELD_NO_NARRATIVE_DETERMINISTIC = "no_narrative_deterministic"

_WITHHELD_DEFAULT = (
    "Draft narrative withheld; the governed query results are shown instead."
)

NARRATIVE_WITHHELD_CONTENT: dict[str, str] = {
    WITHHELD_UNSAFE_TEXT: (
        "Draft narrative withheld: the output safety guard flagged its wording. "
        "Governed rows are shown instead."
    ),
    WITHHELD_UNVERIFIED_NUMBERS: (
        "Draft narrative withheld: it carried numbers the returned rows could "
        "not verify."
    ),
    WITHHELD_CONTRADICTED: (
        "Draft narrative superseded: it contradicted the governed recomputation."
    ),
    WITHHELD_NO_NARRATIVE: (
        "Genie returned no narrative; the governed query result is shown without prose."
    ),
    WITHHELD_NO_NARRATIVE_DETERMINISTIC: (
        "Genie returned no narrative, so the verified deterministic summary is shown."
    ),
}
