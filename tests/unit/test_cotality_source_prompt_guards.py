"""The prompt guards refuse the configured Cotality share in qualified form only.

Two surfaces name the share: the growth objective guard
(``_PROMPT_JAILBREAK_RE``, ``instruction_override``) and the Ask Genie
scope-bypass matcher (``_SCOPE_BYPASS_PROMPT_PATTERNS``, ``scope_bypass``).
Their reviewed canonical patterns are unchanged. A configured share adds
exactly its qualified names (``catalog.schema.`` and each table's three-part
name, ``backend/config/source_names.qualified_forms``), each part
``re.escape``'d and spelled as SQL accepts it (quoted parts, spaced dots), on
both surfaces; a bare configured token never becomes a pattern, so a schema
called ``mortgage`` leaves every mortgage question alone.

The battery runs every reviewed bank (the Ask Genie allow and refusal banks,
the no-refusal battery, the growth guard families and reviewed objectives)
in a fresh process per configured source and requires the verdicts to be the
canonical install's, question for question. The qualified-form probes are
ON/OFF pairs: each passes both surfaces on the canonical install, so the
configured form, and nothing else, owns its refusal on a configured one. The
``zyrplax`` catalog is an unbanked token: it refuses only qualified, like any
other configured name.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.config import source_names
from backend.config.source_names import CANONICAL, TABLE_STEMS, CotalitySource
from backend.schemas import _validators_source_names as guard_forms
from tests.fixtures.cotality_source_probe import run_fresh
from tests.unit.test_genie_guardrail_battery import _LEGITIMATE_QUESTIONS, _MUST_REFUSE
from tests.unit.test_genie_no_refusal_battery import VALID_ANALYTICAL_QUESTIONS
from tests.unit.test_growth_agent_refusal_families import _FAMILY_PROMPTS

SOURCES = {
    "acme_prefixed": CotalitySource("acme_share", "mortgage", "acme_"),
    "acme_no_prefix": CotalitySource("acme_share", "mortgage", ""),
    "zyrplax_feed": CotalitySource("zyrplax_feed", "mortgage", "zx_"),
}
REVIEWED_OBJECTIVES = (
    "Find In the Money and listed-for-sale opportunities across current coverage.",
    "Find prime refinance opportunities for a branch manager review.",
    "Find refinance opportunities for branch follow-up.",
    "Find mortgage refinance opportunities for branch follow-up.",
)
BARE_MORTGAGE = (
    "Which mortgage segments have the most in-the-money borrowers?",
    "What is the average mortgage rate spread by state?",
    "How many mortgage borrowers are in the money in Texas?",
    "Show the mortgage portfolio by state and segment.",
)
UNCONFIGURED_CONTROL = "How many borrowers are in zyrplax_other.mortgage.property_domain_v3?"


def _env(source: CotalitySource) -> dict[str, str]:
    return {
        source_names.CATALOG_ENV: source.catalog,
        source_names.SCHEMA_ENV: source.schema,
        source_names.TABLE_PREFIX_ENV: source.table_prefix_setting,
    }


def _probe(name: str) -> str:
    return f"How many borrowers are in {name}?"


def _bare_probes(source: CotalitySource) -> list[str]:
    """Each configured name unqualified, and the two-part schema.table form."""

    bare = [source.catalog, source.schema, f"{source.catalog}.{source.schema}"]
    for stem in TABLE_STEMS:
        bare += [source.table_name(stem), f"{source.schema}.{source.table_name(stem)}"]
    return [_probe(name) for name in bare]


def _quoted(form: str, quote: str) -> str:
    return ".".join(f"{quote}{part}{quote}" if part else part for part in form.split("."))


def _qualified_probes(source: CotalitySource) -> list[str]:
    """Each qualified form, upper-cased, part-quoted and dot-spaced as SQL accepts."""

    forms = source_names.qualified_forms(source)
    table = forms[1]
    catalog_schema, _, table_name = table.rpartition(".")
    spelled = [
        table.upper(),
        _quoted(table, "`"),
        _quoted(forms[0], "`"),
        _quoted(table, '"'),
        f"`{catalog_schema}`.{table_name}",
        table.replace(".", " . "),
        forms[0].replace(".", " ."),
        table.replace(".", ". "),
    ]
    return [_probe(form) for form in (*forms, *spelled)]


_BANKS = (
    *_LEGITIMATE_QUESTIONS,
    *(question for question, _ in _MUST_REFUSE),
    *VALID_ANALYTICAL_QUESTIONS,
    *(prompt for prompt, _ in _FAMILY_PROMPTS),
    *REVIEWED_OBJECTIVES,
    *BARE_MORTGAGE,
    UNCONFIGURED_CONTROL,
    *(_probe(CANONICAL.table(stem)) for stem in TABLE_STEMS),
)
_QUESTIONS = sorted(
    {
        *_BANKS,
        *(q for source in SOURCES.values() for q in _bare_probes(source)),
        *(q for source in SOURCES.values() for q in _qualified_probes(source)),
    }
)


@pytest.fixture(scope="module")
def verdicts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    """Every question's verdicts per source, one fresh process each, run side by side."""

    cwd = tmp_path_factory.mktemp("cotality-prompt-guards")
    envs = {"canonical": {}, **{case: _env(source) for case, source in SOURCES.items()}}
    with ThreadPoolExecutor(max_workers=len(envs)) as pool:
        futures = {
            case: pool.submit(run_fresh, env, "verdicts", cwd, _QUESTIONS)
            for case, env in envs.items()
        }
        return {case: future.result() for case, future in futures.items()}


@pytest.mark.parametrize("case", SOURCES)
def test_every_bank_and_bare_token_keeps_its_canonical_verdict(
    verdicts: dict[str, dict], case: str
) -> None:
    unchanged = [*_BANKS, *_bare_probes(SOURCES[case])]
    changed = {q for q in unchanged if verdicts[case][q] != verdicts["canonical"][q]}

    assert changed == set()


@pytest.mark.parametrize("case", ["canonical", *SOURCES])
def test_the_allow_bank_stays_allowed_and_the_refusal_bank_refused(
    verdicts: dict[str, dict], case: str
) -> None:
    result = verdicts[case]

    assert [q for q in _LEGITIMATE_QUESTIONS if result[q]["genie"]] == []
    assert [q for q in VALID_ANALYTICAL_QUESTIONS if result[q]["genie"]] == []
    assert [q for q, cls in _MUST_REFUSE if cls not in result[q]["genie"]] == []
    assert [p for p, code in _FAMILY_PROMPTS if result[p]["growth"] != code] == []
    assert [q for q in (*REVIEWED_OBJECTIVES, *BARE_MORTGAGE) if result[q]["growth"] != "allowed"] == []
    assert [q for q in BARE_MORTGAGE if result[q]["genie"]] == []
    for stem in TABLE_STEMS:
        canonical_reference = result[_probe(CANONICAL.table(stem))]
        assert "scope_bypass" in canonical_reference["genie"]
        assert canonical_reference["growth"] == "instruction_override"


@pytest.mark.parametrize("case", SOURCES)
def test_qualified_configured_names_refuse_on_both_surfaces(
    verdicts: dict[str, dict], case: str
) -> None:
    for question in _qualified_probes(SOURCES[case]):
        # OFF: the canonical install lets the same question through both guards.
        assert verdicts["canonical"][question] == {"genie": [], "growth": "allowed"}, question
        # ON: only the configured form makes it a refusal.
        assert verdicts[case][question] == {
            "genie": ["scope_bypass"],
            "growth": "instruction_override",
        }, question


def test_a_name_another_install_configures_is_not_refused_here(verdicts: dict[str, dict]) -> None:
    acme = _qualified_probes(SOURCES["acme_prefixed"])
    for question in [*acme, UNCONFIGURED_CONTROL]:
        assert verdicts["zyrplax_feed"][question] == verdicts["canonical"][question], question


def test_the_canonical_source_adds_no_form() -> None:
    assert source_names.qualified_forms(CANONICAL) == ()
    assert guard_forms._compiled(()) == ()


@pytest.mark.parametrize("source", SOURCES.values(), ids=SOURCES)
def test_every_form_is_qualified_and_no_bare_token_matches(source: CotalitySource) -> None:
    forms = source_names.qualified_forms(source)
    (pattern,) = guard_forms._compiled(forms)

    assert forms[0] == f"{source.catalog}.{source.schema}."
    assert forms[1:] == tuple(source.table(stem) for stem in TABLE_STEMS)
    assert all(guard_forms._QUALIFIED_FORM.fullmatch(form) for form in forms)
    for question in _bare_probes(source):
        assert pattern.search(question) is None, question
    for name in (source.catalog, source.schema, f"{source.catalog}.{source.schema}"):
        # Quoted or spaced, a name short of ``catalog.schema.`` is still bare.
        for spelled in (_quoted(name, "`"), _quoted(name, '"'), name.replace(".", " . ")):
            assert pattern.search(_probe(spelled)) is None, spelled
    assert pattern.search(f"use {source.catalog}.{source.schema}.anything") is not None
    assert pattern.search(f"use `{source.catalog}` . `{source.schema}` . anything") is not None
    assert pattern.search(f"x{source.catalog}.{source.schema}.") is None
    assert pattern.search(f"x {source.catalog}x.{source.schema}.") is None


@pytest.mark.parametrize(
    "form",
    ["mortgage", "acme_share", "acme_share.mortgage", "mortgage.property_domain_v3", ".mortgage.", "a.b.c.d"],
)
def test_an_unqualified_form_refuses_to_compile(form: str) -> None:
    with pytest.raises(ValueError, match="catalog.schema.-qualified"):
        guard_forms._compiled(("acme_share.mortgage.", form))


def test_a_regex_metacharacter_in_a_form_is_escaped() -> None:
    (pattern,) = guard_forms._compiled(("a_b.c_d.", "a_b.c_d.t_1"))

    assert pattern.search("a_b.c_d.") is not None
    assert pattern.search("aXb.c_d.") is None
    assert pattern.search("a_bXc_d.") is None
    assert pattern.search("a_b c_d.") is None


def test_importing_settings_registers_the_configured_forms(tmp_path: Path) -> None:
    import backend.config.settings  # noqa: F401 - the registration is an import side effect

    assert guard_forms._forms_provider is not None
    assert guard_forms.configured_source_patterns() == ()
