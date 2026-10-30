"""Every lender call site resolves through reviewed_lender_identity_from_env.

Two contracts, call site by call site:

* D0 parity. With no pinned record, each site resolves exactly what it did
  before: the App payload emits the live install's 32-name env set (the set
  the emergency recipe gates on), Settings, branding and the Lakebase
  disclosure seed are byte-identical (golden digests taken on origin/main
  a9493e21, before this change).
* A customer record is accepted only when pinned. An unpinned, mispinned or
  tampered record refuses at the payload build (exit 2), in Settings, in
  bundle_env, in the deploy preflight, in the Lakebase seed and in the
  lender-mark tool, and no refusal names a value.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.config.settings import Settings
from backend.schemas import lender_branding
from backend.schemas.lender_identity import (
    effective_public_tenant_id,
    validate_public_lender_identity,
)
from backend.schemas.lender_identity_record import (
    LENDER_IDENTITY_JSON_ENV,
    LENDER_IDENTITY_SHA256_ENV,
    LenderIdentityRefused,
    lender_identity_digest,
)
from jobs.lakebase_migration_seed import _tenant_disclosure_seed_sql
from tests.fixtures.client_profiles import (
    LENDER_IDENTITY,
    env_file_text,
    paychex_shaped_customer_profile,
    paychex_shaped_profile,
)
from tools.branding import lender_mark
from tools.databricks import app_deploy_payload, bundle_env

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "lib" / "deploy_step_preflight_gates.sh"

# The live install's App env-var names (map L2, 2026-10-29; names only).
D0_ENV_NAMES = frozenset(
    {
        "APP_ENV", "DATABRICKS_WAREHOUSE_ID", "GENIE_SPACE_ID", "PGHOST", "LAKEBASE_HOST",
        "LAKEBASE_INSTANCE_NAME", "MIP_LAKEBASE_INSTANCE", "MIP_LIFECYCLE_SYNC_JOB_ID",
        "MIP_FRED_RATES_JOB_ID", "MIP_SILVER_REFRESH_JOB_ID", "MIP_GOLD_REFRESH_JOB_ID",
        "MIP_GROWTH_AGENT_SCHEDULER_JOB_ID", "MIP_COTALITY_ID_MASK_SECRET",
        "MIP_GENIE_ACTION_SECRET_CURRENT", "MIP_DEFAULT_CATALOG", "MIP_DEFAULT_SCHEMA",
        "MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", "MIP_LENDER_NAME", "MIP_LENDER_NMLS_ID",
        "MIP_TENANT_ID", "MIP_GIT_SHA", "MIP_ADMIN_EMAILS", "MIP_ADMIN_IDENTITIES",
        "MIP_APPROVER_EMAILS", "MIP_APPROVER_IDENTITIES", "MIP_RUM_ENABLED",
        "MIP_GENIE_REFUSAL_TEXT_CAPTURE", "MIP_LEADS_WARM_INTERVAL_S", "MIP_WAREHOUSE_KEEP_WARM",
        "MIP_LAKEBASE_SYNC_CATALOG", "MIP_LAKEBASE_SYNC_SCHEMA", "MIP_LAKEBASE_SYNC_TABLES",
    }
)
# The operator values behind D0 (synthetic; names as the live install sets them).
D0_OPERATOR_ENV = {
    "MIP_LENDER_NAME": "Summit Mortgage",
    "MIP_LENDER_NMLS_ID": "123456",
    "MIP_TENANT_ID": "summit",
    "MIP_GIT_SHA": "0123456789ab",
    "MIP_ADMIN_EMAILS": "admin@lender.example",
    "MIP_ADMIN_IDENTITIES": "00000000-0000-4000-8000-000000000006",
    "MIP_APPROVER_EMAILS": "approver@lender.example",
    "MIP_APPROVER_IDENTITIES": "00000000-0000-4000-8000-000000000004",
    "MIP_LAKEBASE_SYNC_CATALOG": "mip_app_state",
    "MIP_LAKEBASE_SYNC_SCHEMA": "mip_sync",
    "MIP_LAKEBASE_SYNC_TABLES": "source_readiness,segment_population,funnel_snapshot_daily",
}
# sha256 of the Summit disclosure seed SQL on origin/main a9493e21, for both
# the bare default (no NMLS id, no tenant) and the D0 values.
SUMMIT_DISCLOSURE_SEED_SHA256 = "5e7188bd1a876b5dd30daf0f699fe6f7997cf5e5fed83aa818dfc077e282a3fa"

RECORD: dict[str, Any] = dict(LENDER_IDENTITY)
RECORD_TEXT = json.dumps(RECORD)
RECORD_SHA256 = lender_identity_digest(RECORD_TEXT)
PINNED = {LENDER_IDENTITY_JSON_ENV: RECORD_TEXT, LENDER_IDENTITY_SHA256_ENV: RECORD_SHA256}
TAMPERED = {**PINNED, LENDER_IDENTITY_JSON_ENV: RECORD_TEXT.replace("Harbor", "Harbour")}
_REFUSED_ENVS = {
    "unpinned": {LENDER_IDENTITY_JSON_ENV: RECORD_TEXT},
    "mispinned": {**PINNED, LENDER_IDENTITY_SHA256_ENV: "0" * 64},
    "tampered": TAMPERED,
    "pin_without_record": {LENDER_IDENTITY_SHA256_ENV: RECORD_SHA256},
}
_LENDER_NAMES = ("MIP_LENDER_NAME", "MIP_LENDER_NMLS_ID", "MIP_TENANT_ID", *PINNED)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(app_deploy_payload, "ENV_LOCAL", tmp_path / ".env.local.absent")
    for name in (
        *app_deploy_payload.NON_SECRET_OPERATOR_VARS,
        *app_deploy_payload.SECRET_RESOURCE_BINDINGS,
        *PINNED,
        "MIP_PRESENTER_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "test-mask-secret")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", "test-action-secret")


def _set(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _payload_env() -> dict[str, dict[str, str]]:
    payload = app_deploy_payload.build_payload(
        source_code_path="/Workspace/app-src/release",
        install_class="demo",
        lakebase_instance="mip-app-state",
        campaign_treatment_runtime_enabled=True,
    )
    return {item["name"]: item for item in payload["env_vars"]}  # type: ignore[index]


# --- the App payload builder ----------------------------------------------------


def test_with_no_record_the_payload_emits_exactly_the_live_env_name_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, D0_OPERATOR_ENV)
    env = _payload_env()

    assert set(env) == D0_ENV_NAMES
    assert {env[name]["value"] for name in ("MIP_LENDER_NAME", "MIP_LENDER_NMLS_ID", "MIP_TENANT_ID")} == {
        "Summit Mortgage",
        "123456",
        "summit",
    }


def test_blank_pinned_names_are_unset_and_add_no_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**D0_OPERATOR_ENV, LENDER_IDENTITY_JSON_ENV: "", LENDER_IDENTITY_SHA256_ENV: " "})

    assert set(_payload_env()) == D0_ENV_NAMES


def test_a_pinned_record_adds_only_the_two_pinned_names(monkeypatch: pytest.MonkeyPatch) -> None:
    operator = {name: value for name, value in D0_OPERATOR_ENV.items() if not name.startswith(("MIP_LENDER", "MIP_TENANT"))}
    _set(monkeypatch, {**operator, **PINNED})
    env = _payload_env()

    assert set(env) == D0_ENV_NAMES | set(PINNED)
    assert env["MIP_LENDER_NAME"]["value"] == RECORD["legal_name"]
    assert env["MIP_LENDER_NMLS_ID"]["value"] == RECORD["nmls_id"]
    assert env["MIP_TENANT_ID"]["value"] == RECORD["tenant_id"]
    assert env[LENDER_IDENTITY_SHA256_ENV]["value"] == RECORD_SHA256
    canonical = env[LENDER_IDENTITY_JSON_ENV]["value"]
    assert canonical == json.dumps(RECORD, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == RECORD_SHA256


@pytest.mark.parametrize("case", sorted(_REFUSED_ENVS))
def test_the_payload_build_refuses_an_unverifiable_record_with_exit_2(
    case: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set(monkeypatch, _REFUSED_ENVS[case])

    with pytest.raises(LenderIdentityRefused):
        _payload_env()
    assert app_deploy_payload.main(["--source-code-path", "/Workspace/app-src/release", "--install-class", "demo"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "app_deploy_payload: refused: MIP_LENDER_IDENTITY" in captured.err
    assert RECORD["legal_name"] not in captured.err and RECORD["nmls_id"] not in captured.err


# --- Settings, branding and the Lakebase disclosure seed ----------------------------


def _settings(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Settings:
    for name in _LENDER_NAMES:
        monkeypatch.delenv(name, raising=False)
    _set(monkeypatch, env)
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("env", [{}, D0_OPERATOR_ENV], ids=["bare", "d0_name_set"])
def test_with_no_record_identity_branding_and_disclosure_are_byte_identical(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The d0 case loads every operator name the live install's payload sets.
    settings = _settings(monkeypatch, env)

    assert (settings.mip_lender_name, settings.mip_lender_nmls_id, settings.effective_tenant_id()) == (
        "Summit Mortgage",
        "123456",
        "summit",
    )
    assert settings.mip_tenant_id == ("summit" if env else None)
    assert not settings.reviewed_lender_identity().pinned
    assert lender_branding.reviewed_lender_mark(settings.mip_lender_name) is None
    seed = _tenant_disclosure_seed_sql(
        lender_name=env.get("MIP_LENDER_NAME", "Summit Mortgage"),
        lender_nmls_id=env.get("MIP_LENDER_NMLS_ID", ""),
        tenant_id=env.get("MIP_TENANT_ID", ""),
    )
    assert hashlib.sha256(seed.encode()).hexdigest() == SUMMIT_DISCLOSURE_SEED_SHA256


def test_settings_take_a_pinned_record_and_keep_its_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, PINNED)

    assert (settings.mip_lender_name, settings.mip_lender_nmls_id, settings.mip_tenant_id) == (
        RECORD["legal_name"],
        RECORD["nmls_id"],
        RECORD["tenant_id"],
    )
    assert settings.effective_tenant_id() == RECORD["tenant_id"]
    assert settings.reviewed_lender_identity().digest == RECORD_SHA256


@pytest.mark.parametrize("case", sorted(_REFUSED_ENVS))
def test_settings_refuse_an_unverifiable_record(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="MIP_LENDER_IDENTITY"):
        _settings(monkeypatch, _REFUSED_ENVS[case])


def test_a_runtime_string_alone_still_cannot_name_a_customer_lender(monkeypatch: pytest.MonkeyPatch) -> None:
    # The invariant the registry comment states: a name is accepted only from
    # the code registry or a digest-pinned record, never as a bare string.
    env = {"MIP_LENDER_NAME": RECORD["legal_name"], "MIP_LENDER_NMLS_ID": RECORD["nmls_id"]}
    with pytest.raises(ValidationError, match="independently reviewed source-controlled"):
        _settings(monkeypatch, env)
    assert _settings(monkeypatch, {**env, **PINNED}).mip_lender_name == RECORD["legal_name"]


def test_the_disclosure_seed_renders_the_pinned_record_and_refuses_a_tampered_one() -> None:
    seed = _tenant_disclosure_seed_sql(
        lender_name=RECORD["legal_name"],
        lender_nmls_id=RECORD["nmls_id"],
        tenant_id=RECORD["tenant_id"],
        lender_identity_json=RECORD_TEXT,
        lender_identity_sha256=RECORD_SHA256,
    )
    assert f"{RECORD['legal_name']}, NMLS #{RECORD['nmls_id']}." in seed
    assert f"'{RECORD['tenant_id']}'" in seed

    with pytest.raises(LenderIdentityRefused):
        _tenant_disclosure_seed_sql(
            lender_name=RECORD["legal_name"],
            lender_nmls_id=RECORD["nmls_id"],
            tenant_id=RECORD["tenant_id"],
            lender_identity_json=TAMPERED[LENDER_IDENTITY_JSON_ENV],
            lender_identity_sha256=RECORD_SHA256,
        )
    # The bundle passes the demo's legacy defaults beside a record: they conflict.
    with pytest.raises(LenderIdentityRefused, match="must equal the pinned"):
        _tenant_disclosure_seed_sql(
            lender_name="Summit Mortgage",
            lender_nmls_id="",
            tenant_id="",
            lender_identity_json=RECORD_TEXT,
            lender_identity_sha256=RECORD_SHA256,
        )


def test_the_migration_job_passes_the_pinned_record_from_the_bundle() -> None:
    bundle = (ROOT / "databricks.yml").read_text(encoding="utf-8")
    assert '"--lender-identity-json=${var.lender_identity_json}"' in bundle
    assert '"--lender-identity-sha256=${var.lender_identity_sha256}"' in bundle
    from jobs import lakebase_migrate

    args = lakebase_migrate.build_parser().parse_args(["--lender-identity-json=", "--lender-identity-sha256="])
    assert (args.lender_identity_json, args.lender_identity_sha256) == ("", "")


# --- bundle_env -------------------------------------------------------------------


# bundle_env's targets since #299: ``ci`` resolves the lender from the
# environment alone (no profile); ``client`` first resolves the client
# profile from ``.env.local`` (a demo one, or a customer one whose pinned
# record the environment supplies), then the same lender values.
_BUNDLE_TARGETS = ("ci", "client")
# Ambient names a client profile resolution must not inherit.
_AMBIENT_PREFIXES = ("MIP_", "DATABRICKS_", "BUNDLE_VAR_", "GENIE_", "LAKEBASE_", "APP_ENV")


def _client_env_local(tmp_path: Path, env: dict[str, str]) -> Path:
    """The client profile beside ``env``: a customer one when ``env`` pins a record."""

    if any(name in env for name in PINNED):
        profile = paychex_shaped_customer_profile(**dict.fromkeys(PINNED))
    else:
        profile = paychex_shaped_profile()
    path = tmp_path / ".env.local"
    path.write_text(env_file_text(profile), encoding="utf-8")
    return path


def _bundle_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: dict[str, str], target: str = "ci"
) -> tuple[int, dict[str, str]]:
    seen: dict[str, str] = {}
    if target == "client":
        for name in [name for name in os.environ if name.startswith(_AMBIENT_PREFIXES)]:
            monkeypatch.delenv(name)
        monkeypatch.setattr(bundle_env, "ENV_LOCAL", _client_env_local(tmp_path, env))
    else:
        monkeypatch.setattr(bundle_env, "ENV_LOCAL", tmp_path / ".env.local.absent")
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path)
    for name in ("DATABRICKS_BUNDLE_ROOT", "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS", *_LENDER_NAMES):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BUNDLE_VAR_lender_identity_json", "ambient")
    _set(monkeypatch, env)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))

    def fake_run(command: list[str], *, env: dict[str, str], check: bool) -> object:
        seen.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", target])
    return bundle_env.main(), seen


@pytest.mark.parametrize("target", _BUNDLE_TARGETS)
def test_bundle_env_passes_only_a_verified_record_to_the_bundle(
    target: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, seen = _bundle_env(monkeypatch, tmp_path, {}, target)
    assert code == 0
    assert (seen["BUNDLE_VAR_lender_name"], seen["BUNDLE_VAR_tenant_id"]) == ("Summit Mortgage", "summit")
    assert "BUNDLE_VAR_lender_identity_json" not in seen and "BUNDLE_VAR_lender_identity_sha256" not in seen

    code, seen = _bundle_env(monkeypatch, tmp_path, PINNED, target)
    assert code == 0
    assert seen["BUNDLE_VAR_lender_name"] == RECORD["legal_name"]
    assert seen["BUNDLE_VAR_lender_identity_sha256"] == RECORD_SHA256
    assert lender_identity_digest(seen["BUNDLE_VAR_lender_identity_json"]) == RECORD_SHA256


# Review of #298: bundle_env and the preflight hand the registry their legacy
# values as given, blank and whitespace-only included, exactly as origin/main
# a9493e21 did (the two base formulas below); only the pinned names drop blanks.
_LEGACY_VALUES = {
    "MIP_LENDER_NAME": (None, "", "   ", "Summit Mortgage", " Summit  Mortgage "),
    "MIP_LENDER_NMLS_ID": (None, "", "  ", "123456"),
    "MIP_TENANT_ID": (None, "", "  ", "summit"),
}
_LEGACY_ENVS = [
    {name: value for name, value in zip(_LEGACY_VALUES, values, strict=True) if value is not None}
    for values in itertools.product(*_LEGACY_VALUES.values())
]


def _base_identity(name: object, nmls: object, tenant: object) -> tuple[str, str, str] | None:
    try:
        lender, nmls_id = validate_public_lender_identity(name, nmls)
        return lender, nmls_id, effective_public_tenant_id(tenant, lender_name=lender)
    except ValueError:
        return None


@pytest.mark.parametrize("target", _BUNDLE_TARGETS)
@pytest.mark.parametrize("env", _LEGACY_ENVS, ids=[repr(env) for env in _LEGACY_ENVS])
def test_bundle_env_resolves_the_legacy_values_exactly_as_before(
    env: dict[str, str], target: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = _base_identity(
        env.get("MIP_LENDER_NAME") or "Summit Mortgage", env.get("MIP_LENDER_NMLS_ID"), env.get("MIP_TENANT_ID")
    )
    code, seen = _bundle_env(monkeypatch, tmp_path, env, target)

    if expected is None:
        assert (code, seen) == (2, {})
    else:
        assert code == 0
        assert tuple(seen[f"BUNDLE_VAR_{name}"] for name in ("lender_name", "lender_nmls_id", "tenant_id")) == expected


@pytest.mark.parametrize("target", _BUNDLE_TARGETS)
@pytest.mark.parametrize("case", sorted(_REFUSED_ENVS))
def test_bundle_env_refuses_an_unverifiable_record(
    case: str, target: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, seen = _bundle_env(monkeypatch, tmp_path, _REFUSED_ENVS[case], target)

    assert code == 2 and seen == {}
    err = capsys.readouterr().err
    assert RECORD["legal_name"] not in err
    # Refused by the record's own check, never by the target: -t ci at
    # bundle_env's lender resolution, -t client already at the profile.
    if target == "client":
        assert "[bundle_env] client profile refused:" in err and "MIP_LENDER_IDENTITY" in err
    else:
        assert "[bundle_env] invalid lender disclosure identity: MIP_LENDER_IDENTITY" in err


# --- the deploy preflight -----------------------------------------------------------

_BLOCK_START = '_MIP_LENDER_NAME_CONFIGURED="$(deployment_control_value MIP_LENDER_NAME)"'
_BLOCK_END = 'echo "[deploy] lender identity: digest-pinned record ${MIP_LENDER_IDENTITY_SHA256:0:12}"\nfi\n'


# The preflight's lender exports, up to (not including) the next export.
_EXPORTS_START = 'export BUNDLE_VAR_lender_name="$MIP_LENDER_NAME"\n'
_EXPORTS_NEXT = 'export BUNDLE_VAR_lakebase_instance_name="$MIP_LAKEBASE_INSTANCE"\n'
# What a child process (the bundle CLI) inherits of the record's two variables.
_BUNDLE_RECORD_VARS = "bash -c 'echo \"bundle=[${BUNDLE_VAR_lender_identity_sha256-unset}|${BUNDLE_VAR_lender_identity_json-unset}]\"'\n"


def _between(text: str, start_marker: str, end_marker: str, *, inclusive: bool = True) -> str:
    start = text.index(start_marker)
    return text[start : text.index(end_marker, start) + (len(end_marker) if inclusive else 0)]


def _run_preflight_block(
    cwd: Path, env: dict[str, str], *, through_bundle_exports: bool = False
) -> subprocess.CompletedProcess[str]:
    text = PREFLIGHT.read_text(encoding="utf-8")
    block = _between(text, _BLOCK_START, _BLOCK_END)
    exports = ""
    if through_bundle_exports:
        exports = _between(text, _EXPORTS_START, _EXPORTS_NEXT, inclusive=False) + _BUNDLE_RECORD_VARS
    # The deploy's own control readers, with .env.local never read: the shell only.
    harness = (
        "set -euo pipefail\n"
        "RED=''; RST=''\n"
        f'. "{ROOT / "scripts" / "lib" / "deploy_identity_lifecycle.sh"}"\n'
        "dotenv_value() { printf ''; }\n"
        f"{block}"
        'echo "identity=[${MIP_LENDER_NAME}|${MIP_LENDER_NMLS_ID}|${MIP_TENANT_ID}|${MIP_LENDER_IDENTITY_SHA256:-}]"\n'
        f"{exports}"
    )
    base = {key: value for key, value in os.environ.items() if not key.startswith("MIP_")}
    return subprocess.run(
        ["bash", "-c", harness],
        cwd=cwd,
        env={**base, "PYTHON": sys.executable, "PYTHONPATH": str(ROOT), **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_preflight_resolves_the_demo_exactly_as_before() -> None:
    completed = _run_preflight_block(ROOT, {})

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "identity=[Summit Mortgage|123456|summit|]"


def test_the_preflight_exports_a_verified_record_without_printing_it() -> None:
    completed = _run_preflight_block(ROOT, PINNED)

    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.strip().splitlines()
    assert lines[0] == f"[deploy] lender identity: digest-pinned record {RECORD_SHA256[:12]}"
    assert lines[1] == f"identity=[{RECORD['legal_name']}|{RECORD['nmls_id']}|{RECORD['tenant_id']}|{RECORD_SHA256}]"
    assert RECORD_TEXT not in completed.stdout + completed.stderr


def test_only_the_verified_record_reaches_the_bundle_variables() -> None:
    # Review of #298: an ambient BUNDLE_VAR record never reaches the bundle CLI.
    ambient = {"BUNDLE_VAR_lender_identity_json": "ambient", "BUNDLE_VAR_lender_identity_sha256": "ambient"}

    demo = _run_preflight_block(ROOT, ambient, through_bundle_exports=True)
    assert demo.returncode == 0, demo.stderr
    assert demo.stdout.strip().splitlines()[-1] == "bundle=[unset|unset]"

    pinned = _run_preflight_block(ROOT, {**ambient, **PINNED}, through_bundle_exports=True)
    assert pinned.returncode == 0, pinned.stderr
    line = pinned.stdout.strip().splitlines()[-1]
    assert line == f"bundle=[{RECORD_SHA256}|{RECORD_TEXT}]"


@pytest.mark.parametrize(
    "env",
    [{"MIP_LENDER_NAME": "   "}, {"MIP_LENDER_NAME": ""}, {"MIP_LENDER_NMLS_ID": "  "}, {"MIP_TENANT_ID": "  "}],
    ids=["whitespace_name", "empty_name", "whitespace_nmls", "whitespace_tenant"],
)
def test_the_preflight_resolves_the_legacy_values_exactly_as_before(env: dict[str, str]) -> None:
    # The preflight's legacy defaults: a shell value as given, "" when unset.
    expected = _base_identity(
        env.get("MIP_LENDER_NAME") or "Summit Mortgage", env.get("MIP_LENDER_NMLS_ID", ""), env.get("MIP_TENANT_ID", "")
    )
    completed = _run_preflight_block(ROOT, env)

    if expected is None:
        assert completed.returncode == 2, completed.stdout
        assert "lender name, NMLS id, tenant disclosure namespace, or pinned lender record is invalid" in completed.stderr
    else:
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == f"identity=[{'|'.join(expected)}|]"


@pytest.mark.parametrize("case", sorted(_REFUSED_ENVS))
def test_the_preflight_refuses_an_unverifiable_record(case: str, tmp_path: Path) -> None:
    for cwd in (ROOT, tmp_path):  # with the registry, and as the isolated copy without it
        completed = _run_preflight_block(cwd, _REFUSED_ENVS[case])
        assert completed.returncode == 2, (cwd, completed.stdout)
        assert RECORD["legal_name"] not in completed.stdout + completed.stderr


# --- the lender-mark tool -----------------------------------------------------------


def _png(width: int = 64, height: int = 32) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"\x1f\x6f\xb4\xff" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def test_the_mark_tool_takes_a_pinned_records_mark(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _png()
    mark = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "media_type": "image/png",
        "width": 64,
        "height": 32,
        "authorization_ref": "BRAND-AUTH/2026-0042",
        "reviewed_on": "2026-09-30",
    }
    text = json.dumps({**RECORD, "mark": mark})
    _set(monkeypatch, {LENDER_IDENTITY_JSON_ENV: text, LENDER_IDENTITY_SHA256_ENV: lender_identity_digest(text)})
    path = tmp_path / "mark.png"
    path.write_bytes(data)
    argv = ["validate", "--lender", RECORD["legal_name"], "--nmls", RECORD["nmls_id"], "--print-sha256"]

    assert lender_mark.main([*argv, "--file", str(path)]) == 0
    assert capsys.readouterr().out.strip() == mark["sha256"]
    path.write_bytes(_png(66, 33))
    assert lender_mark.main([*argv, "--file", str(path)]) == 2
    assert lender_mark.main(argv) == 2  # the record carries a mark, so the file is required
    assert RECORD["legal_name"] not in capsys.readouterr().err


@pytest.mark.parametrize("case", sorted(_REFUSED_ENVS))
def test_the_mark_tool_refuses_an_unverifiable_record(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, _REFUSED_ENVS[case])

    assert lender_mark.main(["validate", "--lender", RECORD["legal_name"], "--nmls", RECORD["nmls_id"]]) == 2
