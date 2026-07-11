"""S1 canonical-score drift guard.

The high-opportunity threshold and the score display-band edges are each
pinned EXACTLY ONCE PER LAYER:

    UC layer:  sql/uc_functions/fn_high_opportunity.sql   (threshold)
               sql/uc_functions/fn_score_band.sql         (band edges)
    Backend:   backend/services/scoring.py                (constants)
    Frontend:  frontend/src/lib/opportunityScore.ts       (constants)

This module is the grep-style lockstep contract (same family as
tests/unit/test_contact_eligibility.py's fn_* <-> Python parity pins):

1. Parse the numeric constants out of each layer's pinned file and assert
   the three layers agree.
2. Repo-wide scans prove NO stray score-threshold / band-edge literal
   survives outside the pinned sites and test fixtures.
3. Governed side-cars that must legitimately carry the literal (the Genie
   space YAML instructions teach Genie the threshold in prose/SQL) are
   checked for parity instead of banned.
4. Demo-number literals (4.6 / 122000 / 4500000 / 1328 / 79000 / 151 / 91)
   must not appear in production source at all — screen numbers come from
   Unity Catalog, never from hardcoded demo values.

Scope note: the bps-75 minimum rate spread (``mip_min_spread_bps``) is a
DIFFERENT governed constant (offer-rules config seed + settings default)
with its own pinning; these scans are score-scoped on purpose so the two
domains cannot be conflated by a naive number grep.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services.scoring import (
    HIGH_OPPORTUNITY_THRESHOLD,
    SCORE_BAND_HIGH_MIN,
    SCORE_BAND_MED_MIN,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

FN_HIGH_OPPORTUNITY = REPO_ROOT / "sql" / "uc_functions" / "fn_high_opportunity.sql"
FN_SCORE_BAND = REPO_ROOT / "sql" / "uc_functions" / "fn_score_band.sql"
SCORING_PY = REPO_ROOT / "backend" / "services" / "scoring.py"
OPPORTUNITY_TS = REPO_ROOT / "frontend" / "src" / "lib" / "opportunityScore.ts"
GENIE_SPACE_YML = REPO_ROOT / "genie" / "mortgage_lead_intelligence_space.yml"

# Production source trees the stray-literal scans cover. Test fixtures live
# under tests/, sql/fixtures/, frontend/src/mocks/, and *.test.* files —
# excluded by _iter_production_files below.
PRODUCTION_DIRS = ("backend", "frontend/src", "sql", "jobs", "pipelines", "tools", "dashboards")
SOURCE_SUFFIXES = {".py", ".sql", ".ts", ".tsx"}


def _iter_production_files() -> list[Path]:
    files: list[Path] = []
    for tree in PRODUCTION_DIRS:
        base = REPO_ROOT / tree
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if "sql/_rendered/" in rel or "sql/fixtures/" in rel:
                continue
            if "/mocks/" in rel or "/__fixtures__/" in rel or "node_modules" in rel:
                continue
            if ".test." in path.name or path.name.startswith("test_"):
                continue
            files.append(path)
    return files


def _extract(pattern: str, path: Path) -> int:
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    assert match, f"{path.name}: pinned constant not found via {pattern!r}"
    return int(match.group(1))


# ---------------------------------------------------------------------------
# 1. Cross-layer parity: the pinned sites agree on the numbers.
# ---------------------------------------------------------------------------
def test_uc_high_opportunity_threshold_matches_backend_constant() -> None:
    sql_threshold = _extract(r"ELSE opportunity_score >= (\d+)", FN_HIGH_OPPORTUNITY)
    assert sql_threshold == HIGH_OPPORTUNITY_THRESHOLD


def test_uc_score_band_edges_match_backend_constants() -> None:
    sql_high = _extract(r">= (\d+) THEN 'high'", FN_SCORE_BAND)
    sql_med = _extract(r">= (\d+) THEN 'med'", FN_SCORE_BAND)
    assert sql_high == SCORE_BAND_HIGH_MIN
    assert sql_med == SCORE_BAND_MED_MIN


def test_frontend_constants_match_backend_constants() -> None:
    ts_threshold = _extract(r"export const HIGH_OPPORTUNITY_THRESHOLD = (\d+)", OPPORTUNITY_TS)
    ts_high = _extract(r"export const SCORE_BAND_HIGH_MIN = (\d+)", OPPORTUNITY_TS)
    ts_med = _extract(r"export const SCORE_BAND_MED_MIN = (\d+)", OPPORTUNITY_TS)
    assert ts_threshold == HIGH_OPPORTUNITY_THRESHOLD
    assert ts_high == SCORE_BAND_HIGH_MIN
    assert ts_med == SCORE_BAND_MED_MIN


def test_band_ordering_is_sane() -> None:
    """low < med edge < threshold < high edge <= 100 — the product story
    ('75+ are the strongest review candidates', high badge at 85) only
    holds while this ordering does."""
    assert 0 < SCORE_BAND_MED_MIN < HIGH_OPPORTUNITY_THRESHOLD < SCORE_BAND_HIGH_MIN <= 100


# ---------------------------------------------------------------------------
# 2. Stray-literal scans: nothing outside the pinned sites re-declares the
#    numbers in a score predicate or score copy.
# ---------------------------------------------------------------------------
_SCORE_PREDICATE = re.compile(
    r"opportunity_?score[^\n]{0,30}?(?:>=|≥)\s*(\d+)\b", re.IGNORECASE
)
_PINNED_SQL_FILES = {FN_HIGH_OPPORTUNITY, FN_SCORE_BAND}
_PINNED_NUMBERS = {HIGH_OPPORTUNITY_THRESHOLD, SCORE_BAND_HIGH_MIN, SCORE_BAND_MED_MIN}


def test_no_stray_score_threshold_predicates_in_production_source() -> None:
    """`opportunity_score >= <pinned number>` may exist ONLY in the two
    canonical UC function files. Everything else must call
    fn_high_opportunity / fn_score_band (SQL) or interpolate the imported
    constant (Python f-strings carry the placeholder name, not a digit)."""
    offenders: list[str] = []
    for path in _iter_production_files():
        if path in _PINNED_SQL_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for match in _SCORE_PREDICATE.finditer(text):
            if int(match.group(1)) in _PINNED_NUMBERS:
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}: {match.group(0)!r}")
    assert offenders == [], (
        "stray score-threshold literal(s) found — route through "
        "fn_high_opportunity/fn_score_band or import the scoring constant:\n"
        + "\n".join(offenders)
    )


_BAND_EDGE = re.compile(r"(?:>=|≥)\s*(85|65)\b")


def test_no_stray_band_edges_in_frontend_source() -> None:
    """The `.score--high/med/low` tier assignment (>= 85 / >= 65) lives only
    in lib/opportunityScore.ts — a second ternary is a parity bug."""
    offenders: list[str] = []
    for path in _iter_production_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not rel.startswith("frontend/src") or path == OPPORTUNITY_TS:
            continue
        text = path.read_text(encoding="utf-8")
        for match in _BAND_EDGE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{rel}:{line}: {match.group(0)!r}")
    assert offenders == [], (
        "stray band-edge comparison(s) in frontend source — import scoreBand "
        "from lib/opportunityScore instead:\n" + "\n".join(offenders)
    )


_SCORE_PLUS_COPY = re.compile(r"\b(\d{2,3})\+")


def test_no_hardcoded_threshold_copy_in_frontend_source() -> None:
    """Screen copy like "75+" must interpolate HIGH_OPPORTUNITY_SCORE_LABEL;
    a hardcoded fragment silently drifts when the threshold moves."""
    offenders: list[str] = []
    for path in _iter_production_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not rel.startswith("frontend/src") or path == OPPORTUNITY_TS:
            continue
        text = path.read_text(encoding="utf-8")
        for match in _SCORE_PLUS_COPY.finditer(text):
            if int(match.group(1)) == HIGH_OPPORTUNITY_THRESHOLD:
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{rel}:{line}: {match.group(0)!r}")
    assert offenders == [], (
        "hardcoded high-opportunity copy in frontend source — use "
        "HIGH_OPPORTUNITY_SCORE_LABEL / HIGH_OPPORTUNITY_KPI_LABEL:\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# 3. Governed side-cars carry the literal for external engines — parity, not
#    removal.
# ---------------------------------------------------------------------------
def test_genie_space_yaml_thresholds_stay_in_parity() -> None:
    """The Genie space YAML teaches Genie the threshold in instructions and
    trusted SQL. Those literals are provision-time text (not app code), so
    they are pinned by parity: every score predicate there must use the
    canonical threshold or the high band edge."""
    text = GENIE_SPACE_YML.read_text(encoding="utf-8")
    values = {int(v) for v in _SCORE_PREDICATE.findall(text)}
    assert values, "expected the Genie space YAML to describe the score threshold"
    assert HIGH_OPPORTUNITY_THRESHOLD in values, (
        "Genie space YAML no longer teaches the canonical high-opportunity "
        f"threshold ({HIGH_OPPORTUNITY_THRESHOLD})"
    )
    # Stricter what-if simulations (e.g. the 'simulate a stricter top-tier
    # threshold' trusted query) are allowed; anything LOOSER than the
    # canonical threshold would present a second, weaker top-tier notion.
    assert min(values) == HIGH_OPPORTUNITY_THRESHOLD, (
        f"Genie space YAML score predicates {sorted(values)} include a value "
        f"looser than the canonical threshold {HIGH_OPPORTUNITY_THRESHOLD}"
    )


# ---------------------------------------------------------------------------
# 4. Demo-number literals are banned from production source outright.
# ---------------------------------------------------------------------------
_DEMO_TOKENS = ("4.6", "122000", "4500000", "1328", "79000", "151", "91")
_DEMO_PATTERNS = [
    re.compile(rf"(?<![\w.]){re.escape(token)}(?![\w.])") for token in _DEMO_TOKENS
]


def test_no_demo_number_literals_in_production_source() -> None:
    """Design-time demo numbers must never render from source. Every screen
    number resolves from Unity Catalog / Lakebase; the only place these
    tokens may exist is test fixtures (excluded from the scan)."""
    offenders: list[str] = []
    for path in _iter_production_files():
        text = path.read_text(encoding="utf-8")
        for pattern in _DEMO_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{line}: {match.group(0)!r}"
                )
    assert offenders == [], (
        "demo-number literal(s) found in production source:\n" + "\n".join(offenders)
    )
