# Source-tree copy audit — 2026-04-22

> **Note:** This document records a past state. `MIP_MOCK_MODE` has since been removed in the live-data cutover (commit `2f09424`). The text below is preserved for audit traceability.

**Branch:** `fix/copilot-batch-post-merge`
**Scope:** every `frontend/src/**`, `backend/**/*.py`, `docs/**/*.md`, plus
public repo surface (`README.md`, `package.json`, `pyproject.toml`,
`app.yaml`, `databricks.yml`, `.github/**`).
**Driver:** user mandate — "remove ALL demo or otherwise non-enterprise
language EVERYWHERE from the entire repository." The prior sweep (commit
`93b14e6`) was browser-driven; this pass walks the source tree as a
customer / Copilot agent would see it on GitHub.

## Executive summary

| Bucket        | Findings | Blocker | Polish | Nit |
| ------------- | -------- | ------- | ------ | --- |
| Code          |       42 |       9 |     22 |  11 |
| Docs          |       18 |       4 |     11 |   3 |
| Repo surface  |        6 |       2 |      3 |   1 |
| **Total**     |       66 |      15 |     36 |  15 |

Severity calibration:

- **blocker** — would actively hurt us in front of a mortgage-lending
  prospect or a GitHub code reviewer reading the repo cold. Demo-era
  language in README / talk-track, rendered engineer-jargon, false
  docstrings ("Mock mode") that contradict the CLAUDE.md no-mock
  invariant.
- **polish** — internal-sounding but not directly embarrassing. Comments
  referencing "presenter", "booth"; engineer-ese in tertiary surfaces
  (the telemetry strip). Fix in a follow-up sweep.
- **nit** — stylistic: `# Placeholder` file stubs, `TODO:` inside
  engineer-only files, comment wording.

This pass **implements the 15 blockers inline**. Polish + nits are
flagged below for a follow-up sweep.

---

## Part 1 — Code findings

### Blockers (9)

| # | File:line | Current text | Why | Fix |
|---|-----------|--------------|-----|-----|
| C-B1 | `backend/api/portfolio.py:26` | `# Mock mode: criteria don't shift the preview numbers yet; deterministic payload.` | The CLAUDE.md invariant says there is **no mock runtime mode**. This comment is false and a reviewer reading `backend/api/*` cold will ask "what mock mode?" Also visible to a Copilot agent reading the PR diff. | Replace with an accurate comment explaining the repository contract returns the preview deterministically. |
| C-B2 | `backend/services/databricks_sql.py:4` | module docstring: "mock repositories and onto live `mip.gold.*` queries" | Outdated — suggests a mock-repo path exists in prod. | Rewrite the module header to describe the live-UC path without implying a mock-repo swap. |
| C-B3 | `backend/runtime.py:6` | `"MIP_MOCK_MODE fallback; the app runs on real Unity Catalog data or"` | Mentions a removed env var in the entrypoint docstring. A platform engineer looking at `python -m backend.runtime`'s docstring shouldn't be told about a toggle that no longer exists. | Drop the `MIP_MOCK_MODE` reference; keep the "fails visibly" message. |
| C-B4 | `backend/config/settings.py:27` | `"(see .env.example). There is no mock-mode fallback: the app runs "` | Same issue inside the env-validation docstring. | Drop the explicit "mock-mode fallback" phrasing; read as-if there were never one. |
| C-B5 | `frontend/src/components/mortgage/AgentActivityLog.tsx:238` | Footer renders `Written to Lakebase · immutable · exportable to Unity Catalog` | "Lakebase" is an internal product name the Head of Growth will not recognize. `DegradedBanner` already uses friendly names ("operational database") — apply the same copy discipline here. | Replace with `Immutable audit · exportable for compliance review`. |
| C-B6 | `frontend/src/components/mortgage/AgentActivityLog.tsx:157,235` | `probeSuffix` / `Last health probe · probe 230ms` | "probe" is ops jargon. User-facing. | Replace `probe` with `check`; e.g., `Last health check 230 ms`. |
| C-B7 | `frontend/src/components/mortgage/AgentActivityLog.tsx:216,232` | `Warehouse up · tripped` / `Genie up · recovering` | "tripped" / "recovering" are breaker-speak and "Warehouse"/"Genie" are internal product names in the system status strip. A VP Lending sees raw infra vocabulary. | Map warehouse→"analytics warehouse", genie→"AI assistant" (reuse `FRIENDLY_DEP_NAMES` from DegradedBanner) and drop the "tripped" suffix to a neutral "reconnecting". |
| C-B8 | `frontend/src/routes/admin-config.tsx:141-142` | `"Lakebase schema \`mip_app.audit_events\` · append-only · exported nightly to UC for compliance review."` chip: `mip_app.audit_events` | Customer-visible admin surface leaking raw table names and internal product ("Lakebase", "UC"). | Rewrite desc to: `Append-only audit trail, immutable and exported nightly for compliance review.` Chip: `compliance-grade audit`. |
| C-B9 | `frontend/src/routes/borrower-360.tsx:140` | Comment: `enterprise, not demo-fixture. The backend's subject_property field` | "demo-fixture" near rendered JSX in a file a reviewer will open on GitHub. | Replace with `production, not synthetic-fixture.` |

### Polish (22) — flag-only, fix in follow-up

| # | File:line | Current text | Fix idea |
|---|-----------|--------------|---------|
| C-P1  | `frontend/src/types.ts:118` | "slice 8 and drive the richer presenter UX" | drop "presenter", say "workspace" |
| C-P2  | `frontend/src/components/AppContext.tsx:89,114` | "presenters see"/"presenter's preference" | "users see"/"user's preference" |
| C-P3  | `frontend/src/components/ui/FilterSelect.tsx:5` | "presenter-friendly replacement" | "accessible replacement" |
| C-P4  | `frontend/src/components/fx/Reveal.tsx:11` | "as the presenter scrolls" | "as the user scrolls" |
| C-P5  | `frontend/src/components/mortgage/AgentActivityLog.tsx:8` | "presenters never mistake a dead backend" | "operators never mistake" |
| C-P6  | `frontend/src/components/mortgage/GenieAnswer.tsx:8` | "presenter experience is identical" | "user experience is identical" |
| C-P7  | `frontend/src/routes/offer-orchestrator.tsx:18,35` | "presenter can still click through" / "presenter-friendly label" | drop "presenter" |
| C-P8  | `frontend/src/components/mortgage/USChoroplethMap.tsx:28,97,193` | "any state the presenter" / "presenter's eye lands" / "tests/fixtures/mock_population" references | drop presenter-speak |
| C-P9  | `frontend/src/routes/segment-intelligence.tsx:22,117` | "approximations — presenters" / "so the presenter can signal" | drop |
| C-P10 | `backend/agents/*.py` | six files whose entire contents are `# Placeholder` | either implement or delete empty stubs (they show up in `ls backend/agents/` for a reviewer) |
| C-P11 | `backend/schemas/borrower.py`, `backend/services/evidence.py`, `backend/services/cotality_mcp.py` | `# Placeholder` only | same as above |
| C-P12 | `frontend/src/components/mortgage/USChoroplethMap.tsx:16,30,78,391,508` | 5× `TODO:` near rendered map UI | convert to GitHub issue refs or drop |
| C-P13 | `frontend/src/routes/offer-orchestrator.tsx:319` (header "Thresholds applied") | fine on its own, but the rendered value `Object.entries(rec.thresholds_applied)` is run through `humanizeThresholdKey`; any *unknown* key falls back to snake_case→Title. Add a test to pin this. | add unit test |
| C-P14 | `frontend/src/routes/segment-intelligence.tsx:26,140` | "TODO: wire to backend when MSA / county rollups land" | drop or externalize |
| C-P15 | `frontend/src/mocks/fixtureData.ts:157` | `TODO: wire to /api/audit/events` | fine but belongs in a card, not in-file |
| C-P16 | `backend/api/genie.py:5,15` | docstring mentions "safe-corpus fallback … silent-mock-fallback regression" | trim the self-referential forensics |
| C-P17 | `backend/services/repositories/databricks_repo.py:581,622,635,650` | several "fallback"/"catalog-fallback"/"safe-corpus" in comments | fine internally but overuses the word; tighten |
| C-P18 | `backend/services/genie_client.py:493` | "Read the committed `genie/space_id.txt` as a fallback" | fine |
| C-P19 | `backend/services/observability.py:267` | "``msg`` terse for non-JSON fallback handlers (e.g. pytest capture)" | fine |
| C-P20 | `backend/services/pii_redaction.py` | 15+ "fallback" hits in a single file | fine (real engineering concept) but consider "default dictionary" vocabulary |
| C-P21 | `frontend/src/components/mortgage/USChoroplethMap.tsx:82` | "Synthetic per-state facts (preview; see TODO above)" | stamp with "stylized" not "Synthetic" — we're not claiming the numbers are synthetic data, we're stylizing the map |
| C-P22 | `frontend/src/routes/offer-orchestrator.tsx:103` | comment "Marketing-approved outreach copy. The '[first name]' placeholder is intentionally left for the CRM to fill at send-time" | fine — intentional |

### Nits (11)

- C-N1 `frontend/src/test/setup.ts:1` — "Vitest setup placeholder." (test-only file; benign).
- C-N2 `frontend/src/components/ui/Skeleton.tsx:4` — "token-driven placeholder rect" (component name literally describes a placeholder; keep).
- C-N3–N11 — every `// TODO:` inside `USChoroplethMap.tsx`, `segment-intelligence.tsx`, `fixtureData.ts`. None render; GitHub reviewers can see them. Bundle into a follow-up sweep.

---

## Part 2 — Docs findings

### Blockers (4)

| # | File:line | Current | Fix |
|---|-----------|---------|-----|
| D-B1 | `README.md:33` | `- Local mode: \`MIP_MOCK_MODE=true\`` | **False**. No such runtime toggle. A prospect browsing the repo sees the README first — cannot claim a mock mode exists. Remove the line. |
| D-B2 | `README.md:25` | `Databricks SDK/SQL connector stubs` | No longer true — stubs are replaced. Update to `Databricks SDK + SQL connector (live Unity Catalog)`. |
| D-B3 | `README.md:31` | `Agent Bricks/Supervisor roadmap + deterministic orchestrator` | "roadmap" reads as unshipped. Tighten to `deterministic orchestrator (production-ready); Agent Bricks/Supervisor as an optional extension`. |
| D-B4 | `docs/module0-talk-track.md:1,7` | Title `Module 0 — Conference Session Talk Track`; `Venue: Entrada session, conference 2026` | Customer-visible if the repo is public. A prospect is not going to a "conference session." Rename to `Module 0 — Executive Walkthrough`; drop `Venue: …conference 2026`. |

### Polish (11) — flag-only

| # | File | Issue |
|---|------|-------|
| D-P1 | `docs/implementation-plan.md:1,5,173` | "DAIS demo" framing — internal sprint language |
| D-P2 | `docs/module0-real-data-plan.md` (throughout) | references `MIP_MOCK_MODE` as a live invariant; it was removed |
| D-P3 | `docs/data-contract-module0.md:275,419,562,563,564,592` | "booth-demo posture" / "mock-mode" language |
| D-P4 | `docs/runbook.md:3,6,23,344,350` | "backup presenter", "release rehearsal" |
| D-P5 | `docs/module0-rehearsal-checklist.md` | entire file framed around a presenter |
| D-P6 | `docs/credential-kill-drill.md:28,32,33,235` | "release rehearsal" |
| D-P7 | `docs/governance-real-data-review.md` | 20+ "booth" mentions |
| D-P8 | `docs/data-sources-gap-analysis.md:111,120,121,243,35` | "DAIS booth demo" / "for the DAIS booth demo" |
| D-P9 | `docs/security/m2m-oauth-setup.md:274` | "major release rehearsal" |
| D-P10 | `docs/validation/credential-kill-drill.md:66` | "Before every release rehearsal" |
| D-P11 | `docs/validation/human-ux-pass-checklist.md:10,170` | "last gate before a release rehearsal" / "failed past rehearsals" |

### Nits (3)

- D-N1 `AGENTS.md:15` — agent table entry "demo-storyteller — DAIS talk track". Internal; ok.
- D-N2 `docs/module0-real-data-plan.md` — mentions "Slice N" all over. Internal; ok.
- D-N3 `docs/validation/real-data-walk-2026-04-22.md:21` — mentions `.env.local still carries the pre-scrub mip_demo name`. Internal note; ok.

---

## Part 3 — Repo surface

### Blockers (2)

| # | File | Issue | Fix |
|---|------|-------|-----|
| R-B1 | `pyproject.toml:3` | `description = "Databricks-native Mortgage Intelligence Platform Module 0 demo"` | Drop "demo" — this repo metadata shows up in `pip show`, PyPI-alike tooling, GitHub's sidebar. Rename to `"Databricks-native Mortgage Intelligence Platform — Module 0"`. |
| R-B2 | `app.yaml:64` | comment `# catalog name is used even if an older env leaks "mip_demo" in.` | Benign but references a stale catalog name in a public file. Rewrite without the `mip_demo` ref. |

### Polish (3)

- R-P1 `package.json` root — `"name": "mortgage-intelligence-platform"` is fine; no `description` field set (GitHub auto-populates from README). Consider adding a clean one-liner.
- R-P2 `.github/workflows/README.md:11` — "placeholder BUNDLE_VARs, mock-mode" in CI description. Internal; fine.
- R-P3 `.github/workflows/ci.yml:56` — `# pick the in-process mock implementations explicitly.` — internal CI comment, fine.

### Nit (1)

- R-N1 `FILE_MANIFEST.md` — lists `mocks/`, `fixtures/`, `demo-storyteller.md`. These are directory names; fine.

---

## Part 4 — Commit-message observations (read-only, no rewrites)

Last 30 commits on this branch are **clean** — every message is
professional, scoped, and explainable. The following are worth flagging
but **not** worth rewriting:

- Several commits reference "Slice N" (e.g. `feat(slice-13/...)`). That
  vocabulary is internal; a prospect reading the history will decode
  it as a sprint-planning artifact, not a defect. Acceptable.
- No "WIP", "fixup", "tmp", or profanity in any of the last 30. No
  commit claims work that wasn't landed. No embarrassing messages.
- One commit (`9358606`) references "Slice-13 talk track refresh" —
  talk-track rewording is separate (doc blockers above).
- Backtick-escaping in `9a70b48` (`evidence_events.\`timestamp\``) is
  a shell-escape artifact, not a content problem.

No commit rewrites needed.

---

## Prioritized fix list — top 15 for customer exposure

In strict severity × visibility order. The first 15 are the **blockers**
this pass implements.

1. [C-B1] `backend/api/portfolio.py:26` — drop false "Mock mode" comment.
2. [C-B2] `backend/services/databricks_sql.py:4` — rewrite module docstring.
3. [C-B3] `backend/runtime.py:6` — drop `MIP_MOCK_MODE` reference in entrypoint docstring.
4. [C-B4] `backend/config/settings.py:27` — drop "mock-mode fallback" phrasing.
5. [C-B5] `AgentActivityLog.tsx:238` — footer copy: drop "Lakebase / Unity Catalog" product names.
6. [C-B6] `AgentActivityLog.tsx:157,235` — rename "probe" → "check" in the rendered strip.
7. [C-B7] `AgentActivityLog.tsx:216,232` — use friendly dep names + drop breaker-speak in user strip.
8. [C-B8] `admin-config.tsx:141-142` — rewrite Audit settings InfoPanel to drop "Lakebase schema".
9. [C-B9] `borrower-360.tsx:140` — rewrite "demo-fixture" comment.
10. [D-B1] `README.md:33` — drop `Local mode: MIP_MOCK_MODE=true` line.
11. [D-B2] `README.md:25` — drop "stubs" from stack description.
12. [D-B3] `README.md:31` — reword Agent Bricks roadmap line.
13. [D-B4] `docs/module0-talk-track.md:1,7` — rename title + drop "conference 2026".
14. [R-B1] `pyproject.toml:3` — drop "demo" from package description.
15. [R-B2] `app.yaml:64` — drop "mip_demo" mention in comment.

Polish (36) + nits (15) are **flagged only** — a follow-up sweep can
batch them.
