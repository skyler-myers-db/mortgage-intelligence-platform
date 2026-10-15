# Weekly dependency and release-signal report

- Date: `2026-10-14`; commit `0927bb22ab1cc8c58b1ccdbb5e73b80a3fdd488f`
- Result: `exit 1` (gate red)

## Gate (turns the run red)

- `review_date_past` `tests/fixtures/deps_report/synthetic_ci.yml:25` `2026-10-13`

## Warnings

- `review_date_soon` `tests/fixtures/deps_report/synthetic_ci.yml:26` `2026-10-20`

## Advisories

npm audit over the lock (high and critical gate):

| Package | Advisory | Severity | Effect |
|---|---|---|---|
| `example-parser` | [`GHSA-2234-5678-9cfg`](https://github.com/advisories/GHSA-2234-5678-9cfg) | `moderate` | informational |

pip-audit over uv.lock:

| Package | Version | Advisory | Effect |
|---|---|---|---|
| `oauthlib` | `3.3.1` | `PYSEC-2026-4114` | ignored in ci.yml |

## Dated waivers and holds

| Kind | Subject | Date |
|---|---|---|
| `ci.yml review date` | `tests/fixtures/deps_report/synthetic_ci.yml:25` | `2026-10-13` |
| `ci.yml review date` | `tests/fixtures/deps_report/synthetic_ci.yml:26` | `2026-10-20` |
| `file-size allowlist` | `backend/services/example_split_pending.py` | `2026-10-22` |
| `npm hold` | `@babel/core` | `2026-11-15` |
| `npm hold` | `@tanstack/react-query` | `2026-11-15` |
| `pypi hold` | `databricks-sdk` | `2026-11-15` |
| `tool-literal hold` | `gitleaks` | `2026-11-15` |

## npm drift (never gates)

| Package | Pinned | Latest | Published | Gap | Status |
|---|---|---|---|---|---|
| `@babel/core` | `7.29.7` | `8.0.7` | `2026-10-07` | `major` | `held` `Babel 8 waits for React Compiler plugin compatibility (fixture)` until `2026-11-15` |
| `@rolldown/plugin-babel` | `0.2.4` | `0.3.0` | `2026-10-10` | `major` | `pending` |
| `@tanstack/react-query` | `5.100.10` | `5.104.2` | `2026-10-11` | `minor` | `held` `Fixture hold: the reduced-motion Console close (tail T1b)` until `2026-11-15` |
| `react` | `19.3.0` | `19.3.0` | `2026-09-09` | `current` | `current` |
| `vite` | `8.3.2` | `8.4.0` | `2026-10-12` | `minor` | `pending` |
| `web-vitals` | `6.2.2` | `6.2.3` | `2026-10-05` | `patch` | `pending` |

## PyPI drift (never gates)

| Package | Pinned | Latest | Published | Gap | Status |
|---|---|---|---|---|---|
| `databricks-sdk` | `0.103.0` | `0.152.0` | `2026-10-13` | `major` | `held` `genie-03: no chat-mode message cancel (fixture)` until `2026-11-15` |
| `pyjwt` | `2.15.0` | `2.15.1` | `2026-09-28` | `patch` | `pending` |
| `uvicorn` | `0.47.0` | `0.54.0` | `2026-09-25` | `major` | `pending` |

## Workflow tool pins

| Tool | Literal | Latest release | Status | Where |
|---|---|---|---|---|
| `actions/checkout` | `v6` | `7.0.1` | `major` | `tests/fixtures/deps_report/synthetic_ci.yml:16`, `tests/fixtures/deps_report/synthetic_ci.yml:35` |
| `actions/upload-artifact` | `v7` | `7.0.2` | `current` | `tests/fixtures/deps_report/synthetic_ci.yml:36` |
| `bandit` | `1.7.9` | `1.9.4` | `minor` | `tests/fixtures/deps_report/synthetic_ci.yml:23` |
| `databricks/setup-cli` | `v1.7.0` |  | `no release` | `tests/fixtures/deps_report/synthetic_ci.yml:17` |
| `gitleaks` | `8.28.0` | `8.30.1` | `minor` | `tests/fixtures/deps_report/synthetic_ci.yml:21` |
| `gitleaks` | `8.30.1` | `8.30.1` | `current` | `tests/fixtures/deps_report/synthetic_ci.yml:19` |
| `pip-audit` | `2.9.0` | `2.10.1` | `minor` | `tests/fixtures/deps_report/synthetic_ci.yml:23` |
| `playwright-image` | `1.63.0` | `1.64.1` | `minor` | `tests/fixtures/deps_report/synthetic_ci.yml:31`, `tests/fixtures/deps_report/synthetic_ci.yml:33` |
| `postgres-image` | `16.15-bookworm` |  | `pinned (digest)` | `tests/fixtures/deps_report/synthetic_ci.yml:14` |
| `shellcheck` | `0.11.0` | `0.11.0` | `current` | `tests/fixtures/deps_report/synthetic_ci.yml:7` |

## Live validation gate (informational, never gates)

- Verdict: `FAIL` `no_green_run` for `0927bb22ab1cc8c58b1ccdbb5e73b80a3fdd488f`
- A release from this sha would FAIL the gate: `no_green_run`

Full data: the `deps-report` artifact of this run (`deps-report.json`, `deps-report.md`).
