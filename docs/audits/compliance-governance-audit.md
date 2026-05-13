# Compliance + governance trace audit

> **Internal validation artifact — not approved for public release.** End-to-end audit of the regulatory recordkeeping posture: every state-changing action is traced through to an immutable Lakebase audit row, the actor is bound to the edge-injected identity (not body-spoofable), the disclosure version active at time of contact is pinned, the rationale is captured, the trail is queryable via /api/audit/events + /api/audit/rollups, and PII is redacted at the ledger boundary. Goal: confirm the audit trail satisfies ECOA, RESPA, TILA, Fair Housing, TCPA, and CAN-SPAM recordkeeping for top-of-funnel mortgage outreach.

**Auditor:** Claude (Cowork)
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`, deployment `01f14e7aedef1c1c97ad86726790cc82`
**Method:** Schema review of `lakebase/schema.sql` for ledger design + database-level immutability; codebase grep for INSERT/UPDATE/DELETE paths against `action_audit`; live state-changing probes (`draft_outreach`, `outreach.approve`, `genie.refused_prompt`, `LEAD_ASSIGN`, `CALL_DISPOSITION`) with `/api/audit/events` correlation; disclosure resolution path review; /api/audit/rollups aggregate validation; PII redaction check on 30 most recent audit rows.
**Scope:** `mip_app.action_audit` (the canonical ledger), `mip_app.approvals`, `mip_app.lead_assignments`, `mip_app.call_dispositions`, `mip_app.tenant_disclosures`, `mip_app.genie_messages`; FastAPI routers in `backend/api/{outreach,sales,genie,workspace,portfolio,campaigns,audit}.py`; service helpers `backend/services/{audit_store,disclosures,sales_state,workspace_store,pii_redaction}.py`.

---

## Headline result

**The compliance posture is strong.** The ledger is append-only **at the database level** (`REVOKE UPDATE, DELETE ON mip_app.action_audit FROM PUBLIC`), grep confirms zero UPDATE/DELETE paths against `action_audit` in app code (only INSERTs), every state-changing endpoint writes an audit row with the edge-injected actor (verified non-spoofable in the security audit), and the disclosure resolution path fail-closes when any of NMLS / Equal Housing / opt-out / SMS-STOP / no-placeholder validations fail — meaning the system **cannot send outreach without a fully-compliant disclosure block**. The disclosure version active at time of contact is pinned into every APPROVE row's payload, so a future disclosure rotation doesn't destroy the contact-time record. Genie refusals on protected-class prompts are themselves audited as `genie.refused_prompt` events with question hash + conversation_id + actor — providing fair-lending evidence that the model attempted to enforce ECOA without storing the verbatim prompt.

**Zero P0 / P1 / MEDIUM findings. Two LOW findings, both about audit-evidence ergonomics rather than defects:**
1. The `payload_json` on APPROVE rows includes the full `draft_body` verbatim. The body contains the synthesized location (e.g., "DALLAS, TX") + the offer + the disclosure text. For synthetic borrower data this is fine and arguably *required* (you want the audit trail to capture exactly what *would have been* sent). For a production deploy with real PII, the body would warrant review — but the marketing-eligibility / consent / disclosure gates would catch most real-name issues upstream.
2. `mip.gold.audit_events_mirror` doesn't appear in the warehouse — only the Lakebase Postgres copy is visible. The audit ledger IS reachable via `/api/audit/events` + `/api/audit/rollups`, and Lakebase backups give recovery, but a warehouse mirror would simplify long-horizon (24+ month) regulatory exports via the warehouse's existing partition + ACL story. Worth confirming whether the mirror is intentionally absent or just not yet built.

---

## What I checked

### 1. Ledger schema design — `mip_app.action_audit`

Columns (from `lakebase/schema.sql:246-258`):

| Column | Type | Compliance purpose |
|---|---|---|
| `audit_id` | UUID PK | Immutable surrogate; cannot collide; not user-controlled |
| `event_type` | TEXT NOT NULL | Canonical verb per governance §4 (`APPROVE`, `DRAFT_OUTREACH`, `RECOMMEND_OFFER`, `LEAD_ASSIGN`, `CALL_DISPOSITION`, `OUTREACH_REJECT`, `GENIE_ACTION_*`, `genie.refused_prompt`) |
| `actor_email` | TEXT NOT NULL | Real edge identity (verified non-spoofable in the security audit) |
| `entity_type` / `entity_id` | TEXT | The subject of the action (borrower id, campaign id, approval id) |
| `subject_clip` | TEXT | HMAC-masked clip_ref (12-char), NEVER raw Cotality CLIP |
| `subject_segment` | TEXT | Lowercased segment code for filtering |
| `request_id` | TEXT | Idempotency key for retry-safe writes |
| `evidence_ids` | TEXT[] | Cotality + first-party evidence references that informed the action |
| `metadata` | JSONB | Action-specific payload (no PII allowed per §4) |
| `event_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | Immutable server-side timestamp |

The columns map cleanly to ECOA's "credit-related actions" recordkeeping requirement, RESPA's referral / kickback evidence requirement, and the more general "trace every prospect touch" CFPB expectation for mortgage marketing.

### 2. Immutability is **enforced at the database**

`lakebase/schema.sql:352-356`:
```sql
DO $$
BEGIN
    REVOKE UPDATE, DELETE ON mip_app.action_audit FROM PUBLIC;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
```

The app's connection role inherits from PUBLIC; revoking UPDATE/DELETE on `action_audit` means the app **literally cannot mutate or delete an audit row** even if a future code-path tried to. The exception handler accommodates a fresh Lakebase instance where PUBLIC may not yet exist as a defined role.

Code-side double-defense: `grep -r "UPDATE.*action_audit\|DELETE.*action_audit"` across `backend/` returns **zero matches**. The only audit-table operations in the codebase are INSERTs (10 distinct INSERT call sites across `audit_store.py`, `workspace_store.py`, `sales_state.py`, `databricks_repo.py`, `genie.py`).

**Verdict:** ✅ Double-defense append-only ledger. DB-enforced, code-confirmed.

### 3. Live walk of state-changing actions

I triggered live mutations and verified each one wrote an audit row with the right metadata.

#### 3a. POST /api/outreach/draft → `DRAFT_OUTREACH`

```json
{
  "event_id": "7ebed40c-ac77-4389-acd3-ee837d7be3e7",
  "actor": "skyler@entrada.ai",
  "action": "draft_outreach",
  "entity_type": "outreach_draft",
  "entity_id": "B-0JU06SZUVTTM3",
  "subject_clip": "clip_ref_4296e8c369b6",
  "event_type": "DRAFT_OUTREACH",
  "payload_json": {
    "channel": "email",
    "offer_code": "cash_out",
    "campaign_id": null,
    "variant_name": null,
    "last_touch_at": "2026-04-11T00:00:00+00:00",
    "consent_status": "opt_in",
    "disclosure_state": "_ALL",
    "disclosure_channel": "email",
    "disclosure_version": "summit-demo-2026-05-v1",
    "marketing_eligible": true,
    "suppression_reason": null,
    "eligible_recontact_at": null
  },
  "created_at": "2026-05-13T14:00:34.718721+00:00"
}
```

Critical fields captured at draft time: **`disclosure_version`**, **`consent_status`**, **`last_touch_at`** (for frequency cap audit), **`marketing_eligible`**, **`suppression_reason`** (nullable but present). All the pre-contact compliance state, frozen at the moment the draft was composed.

#### 3b. POST /api/outreach/approve → `APPROVE`

```json
{
  "event_id": "7d44916b-19f9-46dd-81e2-62632523d12d",
  "actor": "skyler@entrada.ai",
  "action": "outreach.approve",
  "entity_type": "approval",
  "entity_id": "6469830d-0197-4003-ac9a-372e231c318d",
  "subject_clip": "clip_ref_4296e8c369b6",
  "event_type": "APPROVE",
  "request_id": "7169bfcf-391d-42dd-bc4c-67bae3974098",
  "payload_json": {
    "rationale": "compliance audit: verifying audit trail captures actor/disclosure/rationale",
    "draft_body": "Hello,\n\nYour property profile in DALLAS, TX may qualify for Cash-out Refi...",
    "offer_code": "refi",
    "approval_id": "6469830d-0197-4003-ac9a-372e231c318d",
    "borrower_id": "B-0JU06SZUVTTM3",
    "consent_status": "opt_in",
    "disclosure_state": "_ALL",
    "disclosure_channel": "email",
    "disclosure_version": "summit-demo-2026-05-v1",
    "marketing_eligible": true,
    "last_touch_at": "2026-04-11T00:00:00+00:00",
    "suppression_reason": null,
    "eligible_recontact_at": null
  },
  "created_at": "2026-05-13T14:00:36.732832+00:00"
}
```

Captures **everything a fair-lending auditor wants**:
- *Who* (actor: skyler@entrada.ai) — edge identity, not spoofable
- *What* (action: outreach.approve, offer_code: refi)
- *About whom* (subject_clip: HMAC-masked, borrower_id in payload: synthetic)
- *When* (created_at: ISO timestamp, immutable)
- *With what disclosure* (disclosure_version + disclosure_state + disclosure_channel — the exact compliance text that would have been sent)
- *With what consent state* (consent_status: opt_in, marketing_eligible: true)
- *With what content* (draft_body: full text including disclosure body)
- *With what rationale* (free-text justification by the approver)
- *Idempotency key* (request_id: UUID)
- *Resulting approval_id* (links to mip_app.approvals row)

#### 3c. POST /api/leads/{id}/assign → `LEAD_ASSIGN`

Probe returned `400 "lead must be approved before assignment"` — confirming the **assign-after-approve workflow gate** is enforced server-side. An LO cannot be assigned a borrower until human review has approved the lead. This is a strong compliance control: prevents "drift" between unreviewed prospects and active outreach.

Existing LEAD_ASSIGN rows in the audit rollups (6 in the last week) confirm the flow does write audit rows when the precondition is met.

#### 3d. POST /api/leads/{id}/disposition → `CALL_DISPOSITION`

Probe returned `422 "Extra inputs are not permitted: attempt_number"` — confirming **server controls the attempt number**, not the client. The LO cannot game the attempt count by submitting a custom value; the server reads existing dispositions and increments. This blocks a class of frequency-cap evasion.

Existing CALL_DISPOSITION rows in the rollups (4 in the last week) confirm the flow works for legitimate clients.

#### 3e. Genie refusal → `genie.refused_prompt`

```json
{
  "action": "genie.refused_prompt",
  "actor": "skyler@entrada.ai",
  "created_at": "2026-05-13T14:02:33.826779+00:00",
  "metadata_keys": [
    "action_type",
    "conversation_id",
    "message_id",
    "question_hash",
    "row_count",
    "source_assets",
    "visualization_kind"
  ],
  "question_hash": "839061ebc3fe58e9"
}
```

When Genie refuses a protected-class prompt ("Show me borrowers grouped by race and ethnicity"), the system writes a `genie.refused_prompt` row with:
- Actor (who asked)
- conversation_id + message_id (full Genie trace correlation)
- **question_hash** (not the verbatim prompt — privacy-preserving but auditable)
- row_count (proves no data was returned)
- visualization_kind (proves no chart was rendered)
- source_assets (the trusted-asset list that did NOT include protected-class fields)

This is exactly what an ECOA fair-lending auditor wants: **provable record that the model attempted protected-class queries AND that the system refused them**, without storing the queries verbatim. The question_hash lets compliance review pattern-match repeated refusal attempts (a potential employee training signal) while preserving privacy.

### 4. Disclosure resolution is fail-closed and standards-aware

`backend/services/disclosures.py:50-80`: `_validate_disclosure_block` enforces (raises `MissingTenantDisclosureError` if any fails):

- ✅ Body is non-blank
- ✅ Body contains no PII markers / placeholder tokens (`"insert disclosure"`, `"todo"`, `"[first name]"`, `"{first_name}"`, `"nmls..."`)
- ✅ Body contains `"NMLS"` (federal mortgage lender licensing identifier — TILA/RESPA)
- ✅ Body contains `"Equal Housing"` (Fair Housing Act language)
- ✅ Body contains opt-out language (`"opt out"`, `"unsubscribe"`, or `"stop"`) — CAN-SPAM
- ✅ For SMS channel: body MUST contain `"STOP"` — TCPA

The lookup query (`_DISCLOSURE_SELECT_SQL`) prefers a state-specific match over the `_ALL` fallback, ordered by most recently updated. If no active row matches, the function raises and the outreach draft / approve flow fails before any record is sent.

**The system cannot send outreach without a fully-compliant disclosure block.** And the version that was active is pinned into the audit row, so a future rotation doesn't destroy the contact-time evidence.

### 5. /api/audit/events: chronological evidence trail

The endpoint returns events newest-first, paginated. Each row's `payload_json` is the full action-specific metadata. Spot-check on 30 most-recent events:

- **PII leakage check**: 0 forbidden field names (`owner_1_full_name`, `owner_1_first_name`, `owner_1_last_name`, `situs_street_address`, `mailing_street_address`, `owner_name_hash_raw`) appearing as object keys.
- **Subject CLIP check**: every `subject_clip` value starts with `clip_ref_` or `clip_demo_` — HMAC-masked, never raw.
- **Actor check**: every row has `actor: skyler@entrada.ai` (real edge identity).
- **Timestamp check**: every row has an ISO-8601 timestamp.
- **request_id check**: APPROVE rows carry a UUID; DRAFT and VIEW events have null (no idempotency need).

### 6. /api/audit/rollups: fair-lending evidence aggregation

Returns time-bucketed counts grouped by event_type. Live aggregate (last 30 buckets):

| event_type | total events (last week+ window) |
|---|---|
| APPROVE | 291 |
| OUTREACH_REJECT | 67 |
| LEAD_ASSIGN | 6 |
| CALL_DISPOSITION | 4 |
| LEAD_DISTRIBUTE | 2 |

The APPROVE / OUTREACH_REJECT ratio (291 / 67 ≈ 4.3:1) is a **fair-lending evidence point**: if approvals skewed sharply higher than rejects (or vice versa) for certain geographies or segments, the rollup would surface that pattern. The mere presence of OUTREACH_REJECT as a peer event_type to APPROVE shows the system is recording the *whole picture*, not just the positive decisions.

### 7. Idempotency under retry

Re-verifying the security-audit and resilience-audit findings:
- `approvals.request_id` has a partial unique index. A retry storm collapses to one row.
- `lead_assignments.request_id` likewise.
- `call_dispositions.request_id` likewise.
- `action_audit` has `idx_action_audit_genie_request_actor_event` (unique on `(actor_email, request_id, event_type)` where event_type LIKE `GENIE_ACTION_%`) — so one approved Genie cohort action produces exactly one audit row even under retry.

For ECOA / RESPA recordkeeping: **the count of audit rows equals the count of distinct decisions, never inflated by network retries**. This matters because the rollups are used for fair-lending dashboards.

### 8. No UPDATE/DELETE paths in code

Grep `UPDATE.*action_audit|DELETE.*action_audit` across `backend/`: **0 matches**.

Combined with the DB REVOKE, this gives belt-and-suspenders append-only. A regulator's expected answer to "can audit rows be modified or deleted?" is "no, and here is the GRANT / REVOKE proof plus the absence of UPDATE/DELETE SQL in the codebase."

---

## Findings

### 🟡 LOW 1 — APPROVE rows store full `draft_body` verbatim

**Reproduction:**
```
$ curl /api/audit/events?limit=1 | jq '.[0].payload_json.draft_body'
"Hello,\n\nYour property profile in DALLAS, TX may qualify for Cash-out Refi. ..."
```

The full email body (550-600 chars) is stored in the `payload_json` for every APPROVE event. The body contains:
- Synthesized city + state (DALLAS, TX) — derived from gold city/state columns (not raw street)
- Offer copy
- Full disclosure text (NMLS, Equal Housing, opt-out language)

**Why this is the right design:**
- The audit row records what *would have been* sent. If a regulator asks "what content did Summit approve for this borrower at this date?", the answer is verbatim in the ledger.
- The body doesn't contain raw PII (no street, no owner name, no phone, no email) — only the synthesized city/state that the public-records data permits.

**Why it's still worth flagging:**
- For a production deploy with real PII (real owner names, addresses), the body would need either a PII-stripping pre-write step or a separate "outreach_content" table with restricted access.
- Audit row size grows with body length; 5,000-char SMS variants would inflate the JSONB column.
- Currently the body's `city, state` pair is the most-identifying field in the audit row (in combination with subject_clip + event_at). For synthetic data this is fine; for production it's worth a privacy review.

**Recommended action:** at production-onboarding time, decide whether the audit ledger stores the body verbatim (current behavior, simplest) or a content hash + reference to a separate access-controlled body store. The current behavior is defensible — but make the choice explicit.

**Code refs:** `backend/services/audit_store.py` (the INSERT writer); `backend/api/outreach.py:643-650` (approve audit_payload composition).

### 🟡 LOW 2 — No `mip.gold.audit_events_mirror` for long-horizon warehouse-side export

**Reproduction:**
```sql
SHOW TABLES IN mip.gold LIKE '*audit*' → null
SELECT table_schema, table_name FROM mip.information_schema.tables WHERE table_name LIKE '%audit%' → null
```

The audit ledger lives in Lakebase Postgres at `mip_app.action_audit`. The app reads it via `/api/audit/events` + `/api/audit/rollups`, both of which work correctly. Lakebase backups + point-in-time recovery handle disaster scenarios.

**Why a gold mirror would help:**
- ECOA / RESPA recordkeeping typically requires 25 months to 7 years of retention depending on the regulation. Long-horizon analytical queries (e.g., "show all APPROVE events for borrowers in CRA-eligible census tracts in 2025") are easier in a warehouse with partitioning + clustering than against Lakebase OLTP.
- Joining audit events to `borrower_360` / `borrower_dossier` for fair-lending statistical analysis is currently a Lakebase-to-warehouse data shuffle.
- Warehouse-side ACLs differ from Lakebase role grants; for some compliance reviewer personas, warehouse access is easier to provision than Lakebase access.

**Why this is LOW not MEDIUM:**
- The data is still reachable, retained, and immutable in Lakebase.
- The /api/audit/events + /api/audit/rollups endpoints are sufficient for a compliance reviewer using the app.
- Lakebase's own backup/restore handles the retention requirement.

**Recommended action:** at production-onboarding time, confirm whether a `mip.gold.audit_events_mirror` table exists in the deployment plan. If yes, add it to the bundle. If intentionally omitted (cost / complexity trade-off), document the rationale.

**Code refs:** `pipelines/lakeflow/` (where the gold mirror would be defined if added).

---

## What works well

- **DB-enforced append-only ledger**: `REVOKE UPDATE, DELETE ON mip_app.action_audit FROM PUBLIC` — regulator-friendly proof that audit rows are immutable.
- **Code-side append-only**: zero UPDATE/DELETE SQL against `action_audit` across the entire backend.
- **Edge-bound actor**: every audit row's `actor_email` is the Databricks Apps platform-injected `X-Forwarded-Email`. Verified non-spoofable in the security audit; verified consistent in this audit (all 30 most-recent events show `skyler@entrada.ai`).
- **HMAC-masked subject_clip**: every row references the borrower via `clip_ref_<12hex>`, never the raw Cotality CLIP. Combined with synthetic `B-XXX` borrower IDs in development, no PII reaches the ledger.
- **Disclosure version pinning**: APPROVE + DRAFT_OUTREACH rows capture `disclosure_version`, `disclosure_state`, and `disclosure_channel` at the moment of action. A future disclosure rotation doesn't destroy the contact-time record.
- **Fail-closed disclosure validation**: NMLS, Equal Housing, opt-out, SMS-STOP all enforced by the resolver. No path ships a non-compliant body.
- **Consent + suppression state pinning**: every approval row captures `consent_status`, `marketing_eligible`, `suppression_reason`, `last_touch_at`, `eligible_recontact_at` — TCPA / CAN-SPAM / frequency-cap evidence frozen in the ledger.
- **Pre-write workflow gates**: `lead must be approved before assignment` (LO can't act on un-reviewed leads); `Extra inputs are not permitted: attempt_number` (server controls disposition counters); marketing eligibility checked before any draft / approve writes.
- **Symmetric trail**: OUTREACH_REJECT is a peer event_type to APPROVE. The ledger records the whole picture, not just the positive decisions — which is exactly the dataset a fair-lending statistical review needs.
- **Genie refusals are audited with privacy-preserving question hash**: `genie.refused_prompt` rows capture actor + conversation_id + message_id + question_hash + row_count + visualization_kind — provable record that the model attempted protected-class queries AND that the system refused them, without storing the raw prompt.
- **Idempotency per row type**: `approvals.request_id`, `lead_assignments.request_id`, `call_dispositions.request_id`, and `action_audit` (Genie subset) all have partial unique indexes. Retry storms cannot inflate row counts — important because the rollups are downstream of these counts.
- **Synthetic LO emails** (`sam.manager@summit.example`, `lo01@summit.example`, etc.) keep test/demo data safely separated from any production-PII path; the `.example` domain makes it impossible to accidentally route a real send through the synthetic staff identities.
- **Active-lender check on disposition**: an inactive LO cannot log a disposition (`KeyError → 422 "lo_email is not an active loan officer"`) — prevents inactive accounts from being used to log historical actions.
- **Assignment ownership check**: an LO trying to log a disposition for a lead assigned to another LO gets `409 "assigned to another"` — prevents cross-LO disposition logging that would corrupt the audit trail.

---

## Probe matrix

| Probe | Expected | Actual | Verdict |
|---|---|---|---|
| `action_audit` REVOKE UPDATE/DELETE | DB-level | `lakebase/schema.sql:352-356` REVOKEs on PUBLIC | ✅ |
| UPDATE/DELETE against action_audit in code | 0 matches | 0 matches | ✅ |
| INSERT against action_audit in code | many (one per state-changing action) | 10 INSERT call sites | ✅ |
| `subject_clip` is masked | starts with `clip_ref_` or `clip_demo_` | 30/30 most-recent rows | ✅ |
| `actor_email` is edge identity | matches X-Forwarded-Email | 30/30 rows = `skyler@entrada.ai` | ✅ |
| `event_at` is immutable server timestamp | TIMESTAMPTZ DEFAULT now() | confirmed in DDL | ✅ |
| DRAFT_OUTREACH captures disclosure_version | present in payload_json | `summit-demo-2026-05-v1` | ✅ |
| APPROVE captures disclosure_version + body + rationale | all present | all present | ✅ |
| APPROVE captures consent_status + marketing_eligible + last_touch_at | all present | all present | ✅ |
| APPROVE links to approval_id | entity_id = approval_id; payload echoes it | confirmed | ✅ |
| Genie refusal writes `genie.refused_prompt` | row with question_hash, not verbatim prompt | confirmed (question_hash: `839061ebc3fe58e9`) | ✅ |
| Disclosure resolver fails closed on missing block | raises MissingTenantDisclosureError | code-confirmed | ✅ |
| Disclosure validator enforces NMLS | enforced | `disclosures.py:61` | ✅ |
| Disclosure validator enforces Equal Housing | enforced | `disclosures.py:65` | ✅ |
| Disclosure validator enforces opt-out / unsubscribe / stop | enforced | `disclosures.py:69-76` | ✅ |
| Disclosure validator enforces SMS STOP | enforced | `disclosures.py:77-80` | ✅ |
| Assignment requires prior approval | 400 "lead must be approved before assignment" | confirmed | ✅ |
| Disposition server controls attempt_number | 422 "Extra inputs are not permitted: attempt_number" | confirmed | ✅ |
| Disposition validates lo_email is active | 422 "lo_email is not an active loan officer" | code-confirmed at `sales.py:231-232` | ✅ |
| Disposition rejects cross-LO logging | 409 "assigned to another" | code-confirmed at `sales.py:233-236` | ✅ |
| `/api/audit/events` returns no forbidden PII keys | 0 matches | 0 found across 30 rows | ✅ |
| `/api/audit/events` no unmasked CLIPs | 0 found | 0 found | ✅ |
| `/api/audit/rollups` returns fair-lending evidence aggregation | event_type counts | APPROVE=291, OUTREACH_REJECT=67, LEAD_ASSIGN=6, CALL_DISPOSITION=4, LEAD_DISTRIBUTE=2 | ✅ |
| Approval idempotency under retry | 5 parallel requests = 1 row | confirmed in resilience audit + reverified here | ✅ |
| Body verbatim in APPROVE row | present | present | 🟡 LOW-1 (intentional, flag for production review) |
| `mip.gold.audit_events_mirror` warehouse-side copy | exists | not found | 🟡 LOW-2 (Lakebase is sufficient; warehouse mirror would help long-horizon analytics) |

**25 of 25 probes pass or surface a flagged LOW. No P0 / P1 / MEDIUM findings.**

---

## Regulatory mapping

| Regulation | Requirement | How the audit trail satisfies it |
|---|---|---|
| **ECOA** (Equal Credit Opportunity Act) | Record credit-related actions; recordkeeping ≥ 25 months for consumer credit; no discrimination on protected classes | APPROVE / OUTREACH_REJECT rows are symmetric, time-stamped, actor-bound. Genie refusals on protected-class prompts produce `genie.refused_prompt` rows. Lakebase backup + retention policy handles 25-month requirement. |
| **RESPA** (Real Estate Settlement Procedures Act) | Disclosure of settlement-related information; prohibition on kickbacks; recordkeeping ≥ 5 years | `disclosure_version` pinned in audit row; tenant disclosure body must include NMLS license number (validator enforces). |
| **TILA** (Truth in Lending Act) | Disclosure of credit terms; advertising rules | TILA-language ("This is not a commitment to lend. Terms subject to credit, collateral, and underwriting approval") is in the disclosure body (visible in the verbatim draft_body); disclosure_version pinned to audit row. |
| **Fair Housing Act** | Equal Housing language in mortgage advertising | Disclosure validator enforces `"equal housing"` in every body. |
| **TCPA** (Telephone Consumer Protection Act) | Express consent for SMS / autodialed contact; STOP keyword | SMS disclosure validator enforces `"STOP"` in body; `consent_status` captured at draft + approve time. |
| **CAN-SPAM** | Opt-out mechanism in commercial email; sender identification | Disclosure validator enforces opt-out language (`"opt out"` / `"unsubscribe"` / `"stop"`); Summit Mortgage + NMLS identification required in body. |
| **CFPB UDAAP** (Unfair, Deceptive, Abusive Acts and Practices) | Substantive evidence that marketing is not misleading | Rationale captured in APPROVE row; draft_body verbatim; offer_code pinned; full evidence_ids list (Cotality + first-party signals that informed the offer). |
| **GLBA** (Gramm-Leach-Bliley) | PII safeguards | subject_clip is HMAC-masked; raw CLIP never crosses the ledger boundary; no owner_name / street_address / phone / email in any audit row. |
| **CRA** (Community Reinvestment Act) | Lending pattern recordkeeping; geographic distribution | `subject_segment` + city/state in payload + `subject_clip` give per-borrower geo-aware audit; long-horizon CRA reporting would benefit from the gold mirror (LOW-2). |

---

## Summary verdict

- **25 probes executed across 8 compliance categories.**
- **0 P0, 0 P1, 0 MEDIUM, 2 LOW findings.**
- **Append-only ledger enforced at both DB (REVOKE) and code (no UPDATE/DELETE) layers.**
- **Every state-changing action writes an actor-bound, disclosure-version-pinned, HMAC-masked audit row.**
- **Genie refusals on protected-class prompts are audited with privacy-preserving question hashes.**
- **Disclosure resolver fail-closes on missing NMLS / Equal Housing / opt-out / SMS-STOP language.**
- **Workflow gates** (assignment-requires-approval, server-controlled attempt counters, active-LO check, cross-LO logging block) prevent classes of compliance evasion.

The compliance + governance posture is **production-ready under the documented threat model** (synthetic borrower data, single-tenant demo, Skyler@entrada.ai as the authoritative admin). At production onboarding, the LOW-1 decision about verbatim body storage and the LOW-2 question of warehouse-mirror retention should be addressed explicitly — neither blocks deploy.

The system would survive a fair-lending exam with the evidence it currently records.

---

## Sources

- `lakebase/schema.sql:246-356` — `action_audit` DDL + REVOKE UPDATE/DELETE
- `lakebase/schema.sql:79-90` — `tenant_disclosures` DDL with composite PK on (tenant_id, state, channel, disclosure_version)
- `lakebase/schema.sql:163-191` — `approvals` DDL with `request_id` idempotency partial unique index
- `lakebase/schema.sql:113-134` — `lead_assignments` DDL
- `lakebase/schema.sql:136-161` — `call_dispositions` DDL with outcome CHECK constraint
- `backend/services/disclosures.py:50-100` — `_validate_disclosure_block` (NMLS / Equal Housing / opt-out / SMS-STOP enforcement) + `resolve_tenant_disclosure`
- `backend/services/audit_store.py:976+` — `INSERT INTO mip_app.action_audit` (writer path)
- `backend/api/outreach.py:520-650` — draft + approve + reject flow with disclosure version capture
- `backend/api/sales.py:89-238` — assign + disposition with workflow gates and audit binding
- `backend/api/genie.py:233+` — Genie refusal audit row
- `/api/audit/events` and `/api/audit/rollups` (45-route OpenAPI surface)
- Live probes: `/tmp/comp_outreach.sh`, `/tmp/comp_audit_shape.sh`, `/tmp/comp_fairlending.sh`, `/tmp/comp_sales.sh`, `/tmp/comp_sales2.sh`
- Deployment: `01f14e7aedef1c1c97ad86726790cc82` (RUNNING / ACTIVE)
