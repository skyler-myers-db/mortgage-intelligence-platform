"""One name per segment, and no retired In the Money variant in shipped text.

critic-11 / D-platform-process-g step 10. Three registries name a segment
code: the gold ``meta`` VALUES (``gold_segment_population.sql``), the App's
``backend/services/segment_labels.py`` and the frontend
``segmentMetadata.ts``. They must agree for every code. The retired variants
of "In the Money" ("Prime Refi ...", "refi(nance) economics", "refi screen")
must not reappear in any shipped string the App, the warehouse or Genie
reads; the only hits allowed are the explicit INPUT aliases below (guard and
synonym vocabulary that recognises what a user may still type, plus the two
legacy KPI-prompt keys and the legacy-name map). The allowlist is exact, so
it cannot rot: a stale entry fails as loudly as a new hit.

Scanned: (a) non-docstring ``str`` constants (AST, f-string parts included)
in ``backend/**/*.py``; (b) the non-comment text of ``sql/**/*.sql``, which is
every single-quoted literal, every COMMENT text and every metric-view YAML
body; (c) ``genie/*.yml`` and ``genie/*.md``; (d) the keys and string values
of ``backend/resources/*.json`` and ``frontend/src/**/*.json``. Stdlib only.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from backend.services import economics_scatter, genie_context_scope, growth_agent_workflows, scoring
from backend.services.repositories import (
    databricks_genie_direct_responses,
    databricks_genie_strategy,
)
from backend.services.segment_labels import (
    ITM_RULE_LABEL,
    LEGACY_SEGMENT_NAMES,
    SEGMENT_DISPLAY_LABELS,
)

REPO = Path(__file__).resolve().parents[2]
GOLD_META = REPO / "sql" / "transformations" / "gold_segment_population.sql"
FRONTEND_REGISTRY = REPO / "frontend" / "src" / "lib" / "segmentMetadata.ts"

BAN_RE = re.compile(r"\bprime[- ]refi\w*|\brefi(?:nance)?[- ]economics\b|\brefi screen\b", re.IGNORECASE)
PRIME_RE = re.compile(r"\bprime\b", re.IGNORECASE)

SIX_CORE = {"itm", "listed", "permit", "investor", "equity", "retention"}

# (path, lower-cased hit) -> exact count. INPUT vocabulary only, never display.
ALLOWED_HITS: dict[tuple[str, str], int] = {
    # Frozen guard / intent vocabulary: it recognises what a user may type.
    ("backend/schemas/growth_agent_segment_intent.py", "prime refi"): 1,
    ("backend/services/repositories/databricks_genie_canonical_scopes.py", "prime refi"): 1,
    # Metric-view synonyms Genie matches a user's words against.
    ("sql/metric_views/certified_borrower_opportunity_metric_view.sql", "prime refi"): 1,
    ("sql/metric_views/certified_borrower_opportunity_metric_view.sql", "refi economics"): 1,
    ("sql/metric_views/certified_lead_generation_metric_view.sql", "refi economics"): 1,
    # The two legacy kpiPrompts KEYS ("refi economics screen", "refi economics").
    ("frontend/src/lib/genieContextTemplates.json", "refi economics"): 2,
    # LEGACY_SEGMENT_NAMES: the retired display name, an input alias only.
    ("backend/services/segment_labels.py", "prime refi"): 1,
    # The refi_plus_heloc campaign audience line is GOVERNED text: the fair-
    # lending screen accepts only the closed reviewed list in
    # marketing_selection_reviewed_workflows._REVIEWED_CAMPAIGN_AUDIENCE_DESCRIPTION
    # (a guard this lane may not touch), and every "in the money" rewording
    # is refused there (unreviewed_criterion / protected_class), which would
    # 500 the campaign recommendation. Renaming it needs that reviewed list
    # changed through the guard process first.
    ("backend/services/campaign_intelligence.py", "refinance economics"): 1,
}


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


def python_strings(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.value


def sql_text_without_comments(text: str) -> str:
    """Drop ``--`` and ``/* */`` comments; keep literals, COMMENT text and ``$$`` bodies."""

    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("$$", i):
            end = text.find("$$", i + 2)
            end = n if end < 0 else end + 2
            out.append(text[i:end])
            i = end
        elif text[i] == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and j + 1 < n and text[j + 1] == "'":
                    j += 2
                elif text[j] == "'":
                    break
                else:
                    j += 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _json_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_strings(item)
    elif isinstance(value, str):
        yield value


def swept_texts(root: Path = REPO) -> Iterator[tuple[str, str]]:
    """(repo-relative path, text) for every swept string, in a stable order."""

    def rel(path: Path) -> str:
        return path.relative_to(root).as_posix()

    for path in sorted((root / "backend").rglob("*.py")):
        for text in python_strings(path):
            yield rel(path), text
    for path in sorted((root / "sql").rglob("*.sql")):
        yield rel(path), sql_text_without_comments(path.read_text(encoding="utf-8"))
    for pattern in ("*.yml", "*.md"):
        for path in sorted((root / "genie").glob(pattern)):
            yield rel(path), path.read_text(encoding="utf-8")
    json_paths = [*(root / "backend" / "resources").glob("*.json"), *(root / "frontend" / "src").rglob("*.json")]
    for path in sorted(json_paths):
        for text in _json_strings(json.loads(path.read_text(encoding="utf-8"))):
            yield rel(path), text


def banned_hits(root: Path = REPO) -> Counter[tuple[str, str]]:
    hits: Counter[tuple[str, str]] = Counter()
    for path, text in swept_texts(root):
        for match in BAN_RE.finditer(text):
            hits[(path, match.group(0).lower())] += 1
    return hits


def gold_meta_names() -> dict[str, str]:
    rows = re.findall(
        r"\(\s*'([a-z_]+)'\s*,\s*'([^']+)'\s*,\s*'[^']*'\s*,\s*'#[0-9A-Fa-f]{6}'\s*\)",
        GOLD_META.read_text(encoding="utf-8"),
    )
    return dict(rows)


def frontend_names() -> dict[str, str]:
    source = FRONTEND_REGISTRY.read_text(encoding="utf-8")
    return dict(re.findall(r"code:\s*'([a-z_]+)',\s*name:\s*'([^']+)'", source))


def test_every_code_has_one_name_across_gold_app_and_frontend() -> None:
    gold = gold_meta_names()
    frontend = frontend_names()
    assert len(gold) == 13
    assert set(gold) == set(SEGMENT_DISPLAY_LABELS) == set(frontend)
    for code, label in SEGMENT_DISPLAY_LABELS.items():
        assert gold[code] == label == frontend[code], code
    assert SEGMENT_DISPLAY_LABELS["itm"] == "In the Money"
    assert ITM_RULE_LABEL == "In the Money rule"
    assert LEGACY_SEGMENT_NAMES == {"Prime Refi Candidates": "itm"}


def test_every_consumer_keeps_its_key_set_and_reads_the_canonical_label() -> None:
    six_code_maps = {
        "growth_agent_workflows.SEGMENT_LABELS": growth_agent_workflows.SEGMENT_LABELS,
        "economics_scatter.SEGMENT_DISPLAY_LABELS": economics_scatter.SEGMENT_DISPLAY_LABELS,
        "direct_responses._SEGMENT_DISPLAY_LABELS": databricks_genie_direct_responses._SEGMENT_DISPLAY_LABELS,
        "strategy._SEGMENT_DISPLAY_LABELS": databricks_genie_strategy._SEGMENT_DISPLAY_LABELS,
    }
    for name, mapping in six_code_maps.items():
        assert set(mapping) == SIX_CORE, name
        assert all(mapping[code] == SEGMENT_DISPLAY_LABELS[code] for code in mapping), name
    # The Genie phase-2 phrases cover every code that has templates.
    assert set(genie_context_scope.SEGMENT_PHRASES) == set(SEGMENT_DISPLAY_LABELS) - {"permit_activity"}
    assert genie_context_scope.SEGMENT_PHRASES["itm"] == "in-the-money"


def test_no_label_or_trigger_label_says_prime() -> None:
    labels: list[str] = [*SEGMENT_DISPLAY_LABELS.values(), *frontend_names().values(), *gold_meta_names().values()]
    for workflow in growth_agent_workflows.WORKFLOWS.values():
        labels.extend([workflow.title, workflow.trigger_label, workflow.action_label])
    labels.extend(scoring.SOURCE_DISPLAY_LABELS.values())
    offenders = [label for label in labels if PRIME_RE.search(label) or BAN_RE.search(label)]
    assert offenders == []
    assert growth_agent_workflows.WORKFLOWS["daily_refi_brief"].trigger_label == "In the Money"


def test_the_sweep_reaches_every_surface() -> None:
    """Non-vacuity: each surface contributes text, and the allowlisted inputs are found."""

    surfaces = Counter(path.split("/", 1)[0] for path, _ in swept_texts())
    assert surfaces["backend"] > 1000
    assert surfaces["sql"] > 30
    assert surfaces["genie"] >= 3
    assert surfaces["frontend"] > 50
    stripped = sql_text_without_comments("SELECT 'a -- b' -- refi screen\n/* refi screen */ 'it''s'")
    assert stripped == "SELECT 'a -- b' \n 'it''s'"


def test_no_retired_in_the_money_variant_ships_outside_the_input_allowlist() -> None:
    hits = banned_hits()
    unexpected = {key: count for key, count in hits.items() if key not in ALLOWED_HITS}
    assert unexpected == {}
    stale = {key: (count, hits.get(key, 0)) for key, count in ALLOWED_HITS.items() if hits.get(key, 0) != count}
    assert stale == {}
