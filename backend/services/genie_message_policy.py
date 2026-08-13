"""Input and model-output safety policy for the Genie message route."""

import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field, field_validator

from backend.schemas._validators_person_names import (
    US_STATE_NAMES,
    contains_human_name_shape,
    shares_token_with_person_lexicon,
)
from backend.schemas._validators_protected_class import (
    contains_protected_class_proxy_marketing_text,
    protected_class_marketing_scan,
)
from backend.schemas._validators_unsafe_text import contains_unsafe_ai_text, mask_governed_phrases
from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_place_dimension import normalize_place_value
from backend.services.marketing_scan_observability import (
    observed_minted_suppressions,
    record_minted_suppressions,
)


class GenieMessageRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = Field(default=None, max_length=256)

    @field_validator("question")
    @classmethod
    def _question_must_contain_text(cls, value: str) -> str:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            raise ValueError("question is required")
        return normalized


class GenieProgressRequest(BaseModel):
    """Poll one in-flight Genie message (async lifecycle).

    POST body (not GET query params) so the signed token never lands in
    access logs or proxy URL captures.
    """

    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    progress_token: str = Field(min_length=1, max_length=4_096)


class GenieCompleteRequest(BaseModel):
    """Finish an in-flight Genie message into a governed answer.

    ``question`` must hash-match the token minted at submit, which makes the
    guarded prompt and the completed answer cryptographically the same turn.
    """

    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    progress_token: str = Field(min_length=1, max_length=4_096)
    question: str = Field(min_length=1, max_length=4_000)

    @field_validator("question")
    @classmethod
    def _question_must_contain_text(cls, value: str) -> str:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            raise ValueError("question is required")
        return normalized


_PROTECTED_PROMPT_TERMS = (
    "age",
    "asian",
    "black",
    "disability",
    "disabled",
    "ethnic",
    "ethnicity",
    "familial status",
    "female",
    "gender",
    "hispanic",
    "latino",
    "latina",
    "male",
    "marital status",
    "national origin",
    "native american",
    "pacific islander",
    "pregnant",
    "race",
    "religion",
    "religious",
    "sex",
    "sexual orientation",
    "white",
    "woman",
    "women",
)

# Narrow exemptions for mortgage vocabulary and geographic proper nouns. The
# rest of each question remains subject to the protected-class scan.
_SAFE_PHRASE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![a-z0-9])loan ages?(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])ages? of (?:the )?loans?(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])loan aging(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])lien ages?(?![a-z0-9])", re.IGNORECASE),
    # The geography router treats this reviewed wording as country scope,
    # not borrower national origin. Campaign/outreach validators do not carry
    # this exemption.
    re.compile(
        r"(?<![a-z0-9])canadian borrowers by (?:zip|postal code)(?![a-z0-9])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![a-z0-9])(?:white|black)\s+"
        r"(?:plains|settlement|salmon|center|creek|river|falls|rock|oaks?|"
        r"haven|bluffs?|stone|mountain|hills?|city|county|lake|earth|water|"
        r"sands?|house|hall|bear|fish|hawk|diamond)(?![a-z0-9])",
        re.IGNORECASE,
    ),
    # Governed offer connectors (live probe 2026-08-06): "candidates with
    # offers" / "with what offer" is core product phrasing, but the trailing
    # "with" reads as an audience-criterion connector to the campaign clause
    # machine and refused a plain HELOC ranking ask. Masking only these
    # literal offer connectors keeps unknown-term laundering ("carry zyrplax")
    # fully scannable.
    re.compile(r"(?<![a-z0-9])with (?:what )?offers?(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])heloc[- ]eligible(?![a-z0-9])", re.IGNORECASE),
)


def _mask_safe_phrases(question: str) -> str:
    masked = question
    for pattern in _SAFE_PHRASE_PATTERNS:
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


def protected_prompt_match(question: str) -> str | None:
    """Name why the prompt guard refuses this question, or None.

    Three of the 428 live gold city values make this refuse an ordinary Module
    0 question, each through a different detector: ``TACOMA`` (the ``-oma``
    condition-morphology heuristic), ``HAWAIIAN GARDENS`` (the direct
    vocabulary), and ``INDIAN HEAD PARK`` (the windowed national-origin bank,
    which needs the population noun a prompt supplies and a grid cell does
    not). Captured live 2026-08-12: "Tell me about Tacoma's in-the-money
    borrowers" refused in ~1.7s, and naming the state does not help because
    ``GENIE_GEO_LOCATION_RE`` is an output-path strip that never runs here.

    The governed mask reaches ONE scanner: ``protected_class_marketing_scan``,
    the one that produces those three false positives. The explicit term bank
    and the proxy scan above it deliberately keep reading the UNMASKED prompt.
    That is not caution for its own sake -- 16 of the 27 terms in
    ``_PROTECTED_PROMPT_TERMS`` (``race``, ``gender``, ``male``, ``ethnicity``,
    ...) have no counterpart in the resolver's canary bank, so a gold city
    named ``RACE`` could clear that admission gate and would silently disarm
    this loop if the mask reached it. Masking the narrowest scanner closes
    that by construction instead of by a second gate.
    """

    scannable = _mask_safe_phrases(question)
    for term in _PROTECTED_PROMPT_TERMS:
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        if re.search(pattern, scannable, flags=re.IGNORECASE):
            return term
    if contains_protected_class_proxy_marketing_text(scannable):
        return "protected_class_proxy"
    # Same rejection set as ``contains_protected_class_marketing_text``, split
    # by cause: the marketing scanner also fails closed on criteria outside
    # the reviewed vocabulary ("which zyrplax borrowers ..."), which is not a
    # fair-lending finding and must not be audited as one.
    # NOT ``assume_reviewed_read_only_analytics=True``, even though the ANSWER
    # path asserts exactly that and the mismatch refuses ~50 ordinary asks as
    # ``unreviewed_criterion`` ("Show me borrowers with a rate spread above 150
    # basis points"). Tried on 2026-08-12 and reverted: the criterion machine
    # is the ONLY net catching a health condition outside the reviewed
    # vocabulary, so switching it off here let "Do a thorough analysis of the
    # portfolio. Show borrowers with eczema." through — a case
    # ``test_planned_deep_analysis_guard_capture`` pins closed on purpose.
    # The fix for that class is to extend the reviewed segment-signal
    # vocabulary with the governed Module 0 measures, not to silence the net.
    governed_places, governed_guards = _governed_protected_class_mask_args()
    # A direct caller, so it takes the verdict rather than opening a collector.
    verdict = protected_class_marketing_scan(
        mask_governed_phrases(scannable, governed_places, governed_guards)
    )
    record_minted_suppressions(verdict.suppressions, surface="genie_prompt")
    if verdict.reason == "unreviewed_criterion":
        return "unreviewed_criterion"
    if verdict.reason is not None:
        return "protected_class_language"
    return None


def identity_prompt_match(question: str) -> bool:
    """Reject person-name-shaped prompts before they enter session state.

    Two scoped corrections, both confined to the person-name heuristic and
    neither visible to any other detector:

    * governed city names the title-case pair scan misreads ("Tell me about
      Aliso Viejo borrowers" — 50 of the 428 live gold values), and
    * the sentence-initial function word a question opens with ("Which
      Washington cities ..." reads as the person "Which Washington"; captured
      live 2026-08-12 as the PII refusal).

    Every PII, injection, and protected-class scan still sees the prompt
    unmodified, and a real name still refuses: the mask erases whole governed
    phrases only, and stripping the opening word leaves the name pair intact.
    """

    scannable = mask_governed_phrases(
        _mask_safe_phrases(question), _governed_name_shape_phrases()
    )
    return contains_human_name_shape(
        scannable, sentence_initial_place_terms=_sentence_initial_place_terms()
    )


def _sentence_initial_place_terms() -> tuple[str, ...]:
    """Places before which an opening ``Which``/``The`` is grammar, not a name.

    US states plus the live governed city dimension, MINUS anything sharing a
    token with the person-name lexicons — the same gate #208 put on the other
    two positional strips.

    That exclusion is the whole safety property here. Recognition is not
    exemption (the place is never consumed and still reaches every scanner),
    but the strip removes the word IN FRONT of it, and 229 of the live gold
    values are single tokens that are also family names. Ungated, "Do Medina
    qualifies for a HELOC?" lost its pair and rendered — 464 of 468 place
    terms flipped that way for each of ``Do``/``An``/``No`` (adversarial
    review, 2026-08-12). ``ELIZABETH`` is in this vocabulary and is the exact
    example the lexicon helper's own docstring is written around.

    An unreachable warehouse degrades to the gated state list alone, and an
    empty list disables the strip entirely.
    """

    try:
        from backend.services.genie_place_dimension import get_governed_place_dimension

        governed = tuple(get_governed_place_dimension().known_place_values())
    except Exception:  # noqa: BLE001 — the guard must never fail the request
        governed = ()
    return tuple(
        term
        for term in US_STATE_NAMES + governed
        if not shares_token_with_person_lexicon(term)
    )


def _visible_text_values(value: object) -> list[str]:
    """Flatten rendered response values, including dynamic table keys and cells."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        values: list[str] = []
        for key, item in value.items():
            values.extend(_visible_text_values(key))
            values.extend(_visible_text_values(item))
        return values
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        values = []
        for item in value:
            values.extend(_visible_text_values(item))
        return values
    return []


def _without_allowed_literals(value: str, allowed_literals: Sequence[str]) -> str:
    scrubbed = value
    for literal in sorted(
        {item.strip() for item in allowed_literals if item.strip()},
        key=len,
        reverse=True,
    ):
        scrubbed = re.sub(re.escape(literal), " governed_staff_label ", scrubbed)
    return scrubbed


# "City Name, ST" / "(City Name, ST)" geography references in Genie prose.
# Borrower rows carry the same city/state strings, so citing them in a
# narrative is sanctioned analytics output — but title-case city names
# ("Lake Forest, CA") pattern-match the human-name-shape guard. Strip the
# geography shape before the name-shape scan ONLY. No real-person identity
# can take this shape here: borrower names never render, and display
# identities are synthetic masked IDs.
#
# "Only" is enforced below by handing the stripped copy to
# ``name_shape_value``. It used to be enforced by nothing: the strip was
# applied to ``value`` before ``contains_unsafe_ai_text``, so it deleted text
# from EVERY detector. This pattern matches any 1-4 title-case words followed
# by ", XX" — it does not know a city from a protected class — so
# "Target Hawaiian, HI homeowners" and "Prioritize Black, AL borrowers" were
# erased down to a clean sentence and passed the fair-lending scan. Neither
# string is a governed place (no such city exists in either state); the guard
# had simply stopped reading them.
GENIE_GEO_LOCATION_RE = re.compile(
    r"\(?\b[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3},\s*[A-Z]{2}\b\)?"
)

# A parenthetical qualifier immediately after a masked borrower ID
# ("**B-0YINYSXBPWZBF** (Miramar): ..."). City-only forms lack the ", ST" the
# geography pattern above needs, so they are blanked here — for the name-shape
# scan and nothing else, via ``name_shape_value``.
#
# It is NOT always the city, which is why the blank cannot be gated on the
# governed place dimension. Sweeping the 14 assets bound to the Genie space on
# paychex 2026-08-12 (18,776 distinct values), a borrower-row answer also writes
# `recommended_offer` ("Investor Product"), `listing_status_description`
# ("Active Under Contract", "Coming Soon"), `current_lender_ref` ("Competitor
# Other") and `evidence_events.source_product` ("Owner Link", "Voluntary Lien +
# Market Rates") there — every one a title-case shape the person-name heuristic
# reads as an identity, and none of them a city.
#
# The content class is deliberately narrower than the old `[^)]{1,40}`: letters,
# digits, spaces and the light punctuation those governed values actually use
# (`·` and `+` are in it because live values need them). `@ : = #` are NOT — a
# labelled span (`Analyst: <name>`, `clip: ABC123456`) is not a governed
# descriptor, and admitting it only ever widened the blind spot below.
_MASKED_ID_PARENTHETICAL_RE = re.compile(
    r"B-[0-9A-Z]{13}\*{0,2}[\s:,·—-]*\(([A-Z][A-Za-z0-9 .,'&/·+-]{0,48})\)"
)


# A cell holding only a number (optional sign, digits, one decimal point).
_BARE_NUMERIC_CELL_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")


def genie_visible_text_unsafe(
    value: str,
    *,
    structured_value: bool = False,
    governed_cell_values: frozenset[str] = frozenset(),
) -> bool:
    """Fail-closed scan for one Genie-rendered string on the analytics surface.

    Ask Genie output is a read-only analytics narrative, not campaign copy:
    ranking vocabulary ("candidates are those with the highest opportunity
    scores") is the product's core language, so the campaign audience-formation
    criterion machine is bypassed. Every PII, injection, confidential,
    health-status, and direct protected-class detector stays on.

    ``structured_value`` marks governed table-cell values (already key-redacted
    gold columns such as city or offer labels). Those keep the mechanical-PII,
    injection, and protected-class scans but skip the title-case human-name
    heuristic, which can only false-positive on structured values ("El Paso",
    "San Antonio", "Purchase Mortgage") — gold rows carry no name columns after
    redaction.

    ``governed_cell_values`` holds normalized values from a governed gold
    dimension (see ``backend.services.genie_place_dimension``) that these
    detectors reject as false positives. The exemption is deliberately the
    narrowest shape that can work: it applies ONLY to structured cells, and
    ONLY when the cell's ENTIRE value equals a governed dimension value. It is
    not substring masking, so "black borrowers in TACOMA" is still scanned in
    full, and no prose path can reach it — a model-authored narrative is never
    a ``structured_value``.

    Three relaxations reach the detectors from here, and each one names the
    single scanner it is allowed to touch: the geography strip and the
    name-shape phrase set reach ``contains_human_name_shape``, the governed
    protected-class phrase set reaches
    ``contains_protected_class_marketing_text``. Nothing reaches the PII,
    injection, or confidential scanners, which read ``value`` verbatim.
    """

    if structured_value and normalize_place_value(value) in governed_cell_values:
        return False
    name_shape_phrases: tuple[str, ...] = ()
    protected_class_phrases: tuple[str, ...] = ()
    protected_class_guards: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = ()
    if not structured_value:
        # Prose only. Structured cells already skip the title-case heuristic
        # wholesale, and the full-cell exemption above is a strictly tighter
        # match than phrase masking, so resolving either for them would be a
        # wasted read — and the resolver probes cells through this very
        # function, so a structured path that resolved them would recurse.
        name_shape_phrases = _governed_name_shape_phrases()
        protected_class_phrases, protected_class_guards = (
            _governed_protected_class_mask_args()
        )
    if structured_value and _BARE_NUMERIC_CELL_RE.fullmatch(value.strip()):
        # A governed row cell that is ENTIRELY a number is a measure, not an
        # identity. Large whole numbers otherwise read as phone numbers
        # ($1,250,000,000 -> "1250000000") or raw identifiers and blocked the
        # whole answer (live persona audit 2026-08-07, sales-manager top-20).
        # Real contact data cannot arrive this way: phone/email/SSN columns are
        # stripped by key at the repository boundary, and a formatted phone
        # ("312-555-0142") is not a bare numeric cell, so it still scans.
        return False
    # Resolved BEFORE the collector opens. The governed dimension probes its
    # own cells through this very function, so resolving inside the block
    # would attribute that probe's suppressions to the answer being scanned.
    sentence_initial_place_terms = () if structured_value else _sentence_initial_place_terms()
    with observed_minted_suppressions("genie_cell" if structured_value else "genie_answer"):
        return contains_unsafe_ai_text(
            value,
            include_titlecase=not structured_value,
            assume_reviewed_read_only_analytics=True,
            name_shape_value=_name_shape_scan_copy(value),
            name_shape_allowed_phrases=name_shape_phrases,
            protected_class_guards=protected_class_guards,
            # Prose only, and the same correction the prompt guard opts into: a
            # capitalized word opening a sentence is orthography. Structured cells
            # skip the title-case heuristic wholesale, so it would be a no-op there.
            name_shape_sentence_initial_place_terms=sentence_initial_place_terms,
            protected_class_allowed_phrases=protected_class_phrases,
        )


def _name_shape_scan_copy(value: str) -> str:
    """The copy handed to the person-name heuristic, and to nothing else.

    Scoping these two strips to one scanner (PR #207) stopped them deleting
    text from the fair-lending, PII and confidential scanners. It did not make
    them *safe for the scanner they do reach*: both still blank their span
    unconditionally, so the person-name heuristic is simply switched off inside
    it. On the #207 head, ``**B-0YINYSXBPWZBF** (John Smith)``,
    ``**B-…** (John Smith, CA)`` and ``Reach John Smith, CA today`` all
    rendered.

    Each blank is therefore admitted only when the span shares no token with
    the reviewed person lexicon — the same gate ``genie_place_dimension``
    applies to its governed exemption sets. A governed descriptor
    (``(Miramar)``, ``(Active Under Contract)``, ``Lake Forest, CA``) is still
    blanked; anything carrying a reviewed name keeps scanning. That turns "no
    borrower name can render here" from an assumption about the gold schema
    into something this boundary enforces.

    Positional, not phrase-based: a ``name_shape_allowed_phrases`` entry masks
    every occurrence of the span in the answer, so a name admitted inside the
    parenthetical would also vanish from a later sentence.
    """

    def _blank_non_person(match: re.Match[str]) -> str:
        return match.group(0) if shares_token_with_person_lexicon(match.group(0)) else " "

    scannable = GENIE_GEO_LOCATION_RE.sub(_blank_non_person, value)
    # Keep the masked ID; only its parenthetical is a name-shape false positive.
    return _MASKED_ID_PARENTHETICAL_RE.sub(
        lambda match: (
            match.group(0)
            if shares_token_with_person_lexicon(match.group(1))
            else match.group(0).replace(f"({match.group(1)})", " ")
        ),
        scannable,
    )


def _governed_name_shape_phrases() -> tuple[str, ...]:
    """Governed city values the prose person-name heuristic misreads.

    Never raises and never widens anything but the name-shape scan: an
    unreachable warehouse degrades to no exemption, which is today's
    (fail-closed) behavior.
    """

    try:
        from backend.services.genie_place_dimension import get_governed_place_dimension

        return tuple(get_governed_place_dimension().name_shape_safe_values())
    except Exception:  # noqa: BLE001 — the guard must never fail the request
        return ()


def _governed_protected_class_mask_args() -> tuple[
    tuple[str, ...], tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
]:
    """The fair-lending mask's phrases AND their boundary guards, together.

    Returned as a pair on purpose: a governed value may subtract only ITSELF
    from this scanner, and the guards are what stop it subtracting the
    protected term its run overlaps (``hawaiian gardens`` shares ``hawaiian``
    with ``native hawaiian``). Handing back phrases whose guards failed to
    build would be the UNGUARDED erase — exactly the laundering the guards
    exist to close — so any failure yields no phrases either and the mask is
    skipped entirely. Fail closed, never fail open.

    Five of the 428 live gold values plus ``Oklahoma`` qualify today: ``TACOMA``
    (the ``-oma`` condition-morphology heuristic), ``BLACK DIAMOND``,
    ``HAWAIIAN GARDENS``, ``INDIAN HEAD PARK``, and the state, which hits the
    same morphology. The resolver's admission gate is what makes the set safe
    to subtract from a fair-lending detector — see
    ``genie_place_dimension._disarms_a_protected_class_canary``.
    """

    try:
        from backend.services.genie_place_dimension import (
            get_governed_place_dimension,
            governed_protected_class_mask_guards,
        )

        phrases = tuple(get_governed_place_dimension().protected_class_safe_values())
        return phrases, governed_protected_class_mask_guards(phrases)
    except Exception:  # noqa: BLE001 — the guard must never fail the request
        return (), ()


def _governed_cell_values(override: frozenset[str] | None) -> frozenset[str]:
    """Governed dimension values exempt from the structured-cell scan.

    Resolved once per response, not once per cell: a full breakdown is ~360
    rows wide and this must not become a per-cell lookup. Never raises — an
    unreachable warehouse degrades to no exemption, which is the pre-existing
    (fail-closed) behavior.
    """

    if override is not None:
        return override
    try:
        from backend.services.genie_place_dimension import get_governed_place_dimension

        return get_governed_place_dimension().conflicting_values()
    except Exception:  # noqa: BLE001 — the guard must never fail the request
        return frozenset()


def genie_unsafe_visible_field(
    response: GenieMessageResponse,
    *,
    allowed_literals: Sequence[str] = (),
    governed_cell_values: frozenset[str] | None = None,
) -> str | None:
    """Name the first rendered surface that fails the guard, or None.

    Returns a field LABEL only — never the offending content — so an operator
    can diagnose a block from logs without the log becoming the leak. A
    100%-reproducible production block with no logged reason cost real
    diagnostic time during the 2026-08-07 persona audit.
    """

    labeled: list[tuple[str, str]] = [("answer", response.answer or "")]
    labeled += [("follow_up", q) for q in response.follow_up_questions]
    labeled += [("reasoning_trace", s.kind) for s in response.reasoning_trace]
    labeled += [("reasoning_trace", s.content) for s in response.reasoning_trace]
    if response.proof is not None:
        labeled += [("proof_trace", s.kind) for s in response.proof.reasoning_trace]
        labeled += [("proof_trace", s.content) for s in response.proof.reasoning_trace]
    if response.native_visualization is not None and response.native_visualization.title:
        labeled.append(("native_visualization", response.native_visualization.title))
    if response.visualization is not None:
        labeled += [
            ("visualization", value)
            for value in (
                response.visualization.title,
                response.visualization.reason,
                response.visualization.x,
                response.visualization.y,
                response.visualization.series,
            )
            if value
        ]
    for label, value in labeled:
        if value and genie_visible_text_unsafe(
            _without_allowed_literals(value, allowed_literals)
        ):
            return label
    governed_values = _governed_cell_values(governed_cell_values)
    for value in _visible_text_values(response.table_rows or []):
        if genie_visible_text_unsafe(
            _without_allowed_literals(value, allowed_literals),
            structured_value=True,
            governed_cell_values=governed_values,
        ):
            return "table_rows"
    return None


def genie_response_has_unsafe_visible_text(
    response: GenieMessageResponse,
    *,
    allowed_literals: Sequence[str] = (),
    governed_cell_values: frozenset[str] | None = None,
) -> bool:
    """Check every model-authored text field rendered by the Genie UI."""

    values = [response.answer, *response.follow_up_questions]
    values.extend(step.kind for step in response.reasoning_trace)
    values.extend(step.content for step in response.reasoning_trace)
    if response.proof is not None:
        values.extend(step.kind for step in response.proof.reasoning_trace)
        values.extend(step.content for step in response.proof.reasoning_trace)
    if response.native_visualization is not None and response.native_visualization.title:
        values.append(response.native_visualization.title)
    if response.visualization is not None:
        values.extend(
            value
            for value in (
                response.visualization.title,
                response.visualization.reason,
                response.visualization.x,
                response.visualization.y,
                response.visualization.series,
            )
            if value
        )
    row_values = _visible_text_values(response.table_rows or [])
    governed_values = _governed_cell_values(governed_cell_values)
    return any(
        genie_visible_text_unsafe(_without_allowed_literals(value, allowed_literals))
        for value in values
    ) or any(
        genie_visible_text_unsafe(
            _without_allowed_literals(value, allowed_literals),
            structured_value=True,
            governed_cell_values=governed_values,
        )
        for value in row_values
    )
