# Audit — Governed Asset Metadata Layer (Module 0)

**Date:** 2026-05-21
**Scope:** The new (uncommitted) Asset Metadata feature shipped after the Proof
Layer — `backend/api/assets.py`, `backend/services/asset_metadata.py`,
`backend/schemas/assets.py`, `backend/services/audit_metadata_value_policy.py`,
the `/data-estate/assets/:assetKey` frontend route (`frontend/src/routes/asset.tsx`),
and the supporting wiring (`drawerSources.ts`, `DataEstatePanel.tsx`,
`api.ts`, `queryKeys.ts`, `borrowers.py` proof audit, `audit_store.py`).
**Method:** Full code read → standalone policy tests in sandbox → live Chrome
walkthrough against the deployed app (`mip-app-2543889327043640`).
**Live deployment:** Asset, proof, and glossary surfaces are all deployed and
reachable; the audited session is admin-authenticated.

---

## What the feature is

An **admin-gated, registry-bounded** read surface that returns *sanitized,
non-PII* Unity Catalog metadata for the 16 trusted Module 0 assets (gold tables,
semantic views, ref tables). It is a proof aid — "here is the governed asset
behind this number" — not a catalog browser. It is reachable from the Data
Estate panel and from drawer source links (`/data-estate/assets/<key>`), and the
backend endpoint is `GET /api/admin/assets/{asset_key}/metadata`.

---

## Findings

**0 P0 / 0 P1 / 0 HIGH / 0 MEDIUM / 0 LOW.** One informational note.

### Security & governance — PASS

- **Admin-gated.** The route depends on `AdminDep`; non-admins get 403, which
  the frontend renders as a dedicated "Admin access required" state.
- **Registry-bounded, fail-closed.** `resolve_asset_descriptor` → `_normalize_key`
  resolves only against the 16-entry `_DESCRIPTOR_MAP`; anything else raises
  `AssetNotFoundError` → 404. Verified live: `system.access.table_lineage` → 404.
- **SQL-injection safe.** Every query uses bound parameters
  (`:catalog`/`:schema_name`/`:object_name`/`:asset`) *or* a descriptor-derived
  FQN passed through `_quote_ident` (regex `^[A-Za-z_][A-Za-z0-9_]*$` + backtick
  quoting). The interpolated identifier is never caller-controlled. Sandbox tests
  confirmed rejection of `; DROP`, `--`, `UNION SELECT`, path traversal,
  `mip_app.*`, `mip.silver.*`, `mip.first_party.*`, `system.*`,
  `information_schema.*`, and `hive_metastore.*`.
- **PII redaction, defense-in-depth.** `_SENSITIVE_RE` drops sensitive columns,
  tags, property values, and DDL lines (owner names, owner_link_id, street/
  mailing/situs, email/phone/ssn, raw_*, source_table, current_servicer, CLIP,
  secrets/tokens). `SHOW TBLPROPERTIES` is **allowlisted** (`_SAFE_PROPERTY_RE`)
  to delta feature flags + reader/writer versions only; `DESCRIBE DETAIL` reads
  only `numRecords`/`lastModified`/`sizeInBytes`/`numFiles` (never `location`).
  Complex column types have sensitive nested fields collapsed
  (`ARRAY<STRUCT<redacted_fields: STRING>>`).
- **No raw exception leakage.** `_clip_error` always returns the generic string
  `"warehouse metadata unavailable"`; per-section failures degrade into
  `known_data_gaps` rather than mock fallback (never-mock invariant upheld).

**Live PII probe (most sensitive tables).** `borrower_dossier` (55 cols) and
`borrower_360` (54 cols) returned only borrower-safe columns (borrower_id,
synthetic display_name, city/state/zip, scores, flags). The 4 raw owner/address/
CLIP/source-table columns are correctly hidden ("4 sensitive column(s) hidden",
"2 complex column type(s) … redacted"). The only sensitive-token match across the
full payload was inside the DDL **header comment** ("Storage locations, owners,
grants … intentionally omitted") — a false positive; zero sensitive hits in
non-comment DDL lines.

### Audit-ledger value policy (`audit_metadata_value_policy.py`) — PASS

The proof endpoint audits `VIEW_BORROWER_PROOF` with `source_assets` / `sql_hash`
/ `row_count`, now validated at the central choke point (`_assert_public_safe_values`).
Sandbox-tested 23 cases: `source_assets` accepts gold/semantics/ref + `fn_*`
functions and `mip_app.*`, rejects silver/first_party/system/information_schema/
hive_metastore/cotality-raw, injection chars, and >20 entries; `sql_hash` accepts
12–64 lowercase hex (strict only for the proof action); `row_count` is a bounded
non-negative int (bool/str rejected). All passed.

### Frontend / design parity — PASS

`asset.tsx` uses the prototype's `.surface` BEM, `Chip`, `Skeleton`, and
`PageShell`; renders status/freshness chips, metric cards, a borrower-safe column
table, sanitized DDL block, allowlisted tags/properties, and observed lineage
that links **only to other registered assets** (`assetHrefForSource` returns
`null` otherwise). The frontend `ASSET_KEYS_BY_SOURCE` map (16 entries) is an
**exact match** to the backend `_DESCRIPTORS` registry — no allowlist drift.
Glossary nav item added; `Borrower 360` wires "Show proof"/"Show math" to the
proof drawer with inline `GlossaryTerm`s.

Live: `/data-estate/assets/borrower_dossier` renders cleanly — Rows 5,156,184
(source_readiness), 1.04 GB, Catalog `mip`, "live"/"Fresh" chips, the three
known-gap callouts, and the safe column list. Enterprise polish, zero console
errors.

### Informational (not a finding)

- The Asset "Generated" metric card shows the raw ISO timestamp with microseconds
  (`2026-05-21T15:03:39.767974Z`). Cosmetic only; consider a friendlier format.

---

## No-regression sweep (the filters — emphasis area)

Analytics multi-select filters re-exercised live and via API:

| Check | Result |
| --- | --- |
| Single-state (CA/FL/IL) | 200, distinct totals |
| Multi-select `states=CA,FL,IL` | 200 |
| **Additivity** (CA+FL+IL == multi) | **Exact for every metric**: addressable 3,503,983; in-the-money 103,574; high-opportunity 2,454; offer-recommended 3,029,499; approved 12 |
| Bad input `states=CALIFORNIA` | 422 (length guard) |
| `states=XX` (valid 2-letter, non-state) | 200 → empty set, by design (format check, not membership) |
| UI under `?states=CA,FL,IL` | KPI cards + Pipeline Metrics mirror the API (In the Money **103.57K**, Approved 12); score-distribution axis clean (10→85), no "511" regression |

Proof drawer Escape + backdrop dismiss now work live (prior **LOW 3 closed** —
see `proof-layer-audit.md` v3). Glossary renders all categories/terms with the
"not a statistical confidence interval" disclaimer.

---

## Verdict

The Asset Metadata layer is **production-safe and demo-safe**: admin-gated,
registry-bounded, SQL-injection-proof, PII-redacted with defense-in-depth,
never-mock-compliant, and design-system aligned. Frontend/backend allowlists are
in lockstep. The new audit-metadata value policy correctly governs the proof
endpoint's ledger writes. No prior surface regressed; the filters are
arithmetically consistent end-to-end; and the one carried-over LOW (proof-drawer
Escape/backdrop dismiss) is now closed on the live app with test coverage.

**Findings: 0 P0 / 0 P1 / 0 HIGH / 0 MEDIUM / 0 LOW** (1 cosmetic informational).
Sign-off: **ship-ready.**
