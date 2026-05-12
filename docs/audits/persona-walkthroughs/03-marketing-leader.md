# Persona walkthrough 3 - Marketing Leader

> Internal validation artifact - not approved for public release. This document contains deployment identifiers, workspace references, and implementation notes intended for engineering review.

**Persona:** Maya, Marketing Leader at Summit Mortgage  
**Current deployment validated:** `01f14dbfb9e81300a5e969695671c6d2`  
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`  
**Validation date:** 2026-05-12 America/New_York

## Remediation Status

All P0/P1/P2 findings from the original Maya walkthrough were reviewed. The execution-safety blockers are closed in the current deployment:

| Finding | Status | Current behavior |
|---|---|---|
| P0-M1 CRM suppression and consent missing from lead/segment/draft surfaces | Fixed | `marketing_eligible`, `consent_status`, `suppression_reason`, `last_touch_at`, and `eligible_recontact_at` round-trip through gold borrower/lead models and API responses. Lead, segment, and portfolio surfaces default to eligible-only contactability. |
| P0-M2 Outreach draft ignored consent/frequency cap | Fixed | Draft generation fails closed for suppressed or recently touched borrowers before copy is composed or audit rows are written. |
| P0-M3 Disclosure footer was a placeholder | Fixed | Drafts resolve versioned tenant disclosures from `mip_app.tenant_disclosures`; resolver rejects blank, placeholder, PII-like, or missing-token disclosure rows. Drafts fail closed when no publishable disclosure is configured. |
| P1-M4 No consent/suppression/recency filters | Fixed | Segment Intelligence, Lead Queue, and Portfolio Builder expose contactability, consent, and recency controls. |
| P1-M5 No message variant / A-B structure | Fixed for campaign setup | Campaign create accepts reviewed `message_variants`; variant names are public-safe validated and audit-protected. |
| P1-M6 Campaigns could be created but not listed or advanced | Fixed | Campaign list/get/patch endpoints exist, are owner/admin scoped, and status advancement is audit-logged. |
| P1-M7 `marketing_eligible` existed in source but not live gold | Fixed | Gold refresh projects the marketing eligibility fields used by the API and UI. |
| P1-M8 Single disclosure constant | Fixed for Module 0 | Disclosures are state/channel keyed with `_ALL` fallback. CA/NY overrides and SMS-specific text are seeded. |
| P1-M9 External deliverability/open/click/bounce attribution | Deferred | External marketing automation callbacks remain post-Module-0 scope. No external send path exists in this app, so this is not a send-safety blocker. |
| P2-M10 Send-window scheduling | Fixed for campaign metadata | Campaign create validates reviewed day labels, local start/end, and timezone metadata. |
| P2-M11 Channel cascade | Fixed for campaign metadata | Campaign create validates ordered `email`, `sms`, and `direct_mail` cascade steps. |
| P2-M12 Holdout/control group | Fixed for campaign metadata | Campaign create validates hash-modulo holdout config. |
| P2-M13 Direct-mail channel missing | Fixed | Outreach draft supports `email`, `sms`, and `direct_mail`. |
| P2-M14 Campaign ROI assumptions missing | Fixed for campaign metadata | Campaign create validates budget, conversion, LO capacity, and per-channel cost assumptions. |
| P2-M15 Saved campaign templates | Fixed at saved-campaign level | Saved campaigns are listed and can be reused as a starting point; dedicated template library remains later UX polish. |
| P2-M16 Fair-lending copy review checkpoint | Deferred | Copy linting is tracked with the broader fair-lending compliance tranche. Drafts already block PII/placeholders and require governed disclosures. |

## Current Walkthrough Result

Maya can now:

1. Build a cohort from Segment Intelligence with `CONTACTABILITY`, `CONSENT`, and `RECENCY` filters.
2. Use Lead Queue with the same contactability controls and see marketing eligibility on rows.
3. Save a campaign with eligible-only suppression policy, channel cascade, send window, holdout, and ROI assumptions.
4. Retrieve the saved campaign from the Saved campaigns list.
5. Draft `email`, `sms`, and `direct_mail` outreach only for eligible borrowers.
6. See state/channel disclosure metadata in Offer Orchestrator before approval.
7. Fail closed on suppressed borrowers, unsafe campaign IDs, unsafe variant names, missing disclosures, or placeholder copy.

## Validation Evidence

- `./scripts/deploy.sh --no-confirm --skip-silver` completed against deployment `01f14dbfb9e81300a5e969695671c6d2`.
- Live smoke passed after Lakebase migration, gold refresh, lifecycle sync, and Genie rebind.
- Live hardened probe passed:
  - default `/api/portfolio/preview` returned eligible-only population `233,420`; explicit `marketing_eligibility='Any'` returned full population `5,156,184`;
  - default `/api/leads` returned only `marketing_eligible=true` rows;
  - suppressed/frequency-capped borrower `B-0FSL4B96HG6V4` failed closed on draft;
  - `campaign_id='jane@example.com'`, `variant_name='Call 212-555-1212'`, and `variant_name='Jane Smith'` returned HTTP 422;
  - eligible borrower `B-102FL7THC6Q3L` returned disclosure-backed email, SMS, and direct-mail drafts.
- Live Maya walkthrough passed with campaign `04da0c51-e10e-4a12-a320-ab1250c93254`; screenshots captured under `/tmp/mip-maya-walkthrough-20260512-final3`.
- Live visual/responsive/proof suite passed: 42 Playwright tests, including Genie Answer proof non-overlap.
- Local validation passed:
  - `.venv/bin/ruff check backend tests tools`;
  - `.venv/bin/python -m pytest -q tests/unit`;
  - `npm --prefix frontend run test`;
  - `npm --prefix frontend run lint`;
  - `npm --prefix frontend run build`;
  - focused marketing/audit/security slices.

## Residual Scope

The app still does not send to Marketo, SFMC, HubSpot, SMS gateways, or direct-mail vendors. That is intentional for Module 0: no external outreach is sent automatically. Post-send deliverability attribution and fair-lending copy linting should be implemented as a dedicated compliance/marketing-automation tranche before any production send integration.

## Independent re-validation (Claude, 2026-05-12 09:50 UTC, deployment `01f14dbfb9e81300a5e969695671c6d2`)

| Finding | Engineering claim | Independent live re-validation |
|---|---|---|
| P0-M1 Suppression/consent missing from gold | All four columns round-trip; default cohorts are eligible-only | ✅ Verified. `mip.information_schema.columns` shows `marketing_eligible`, `consent_status`, `suppression_reason`, `last_touch_at`, `eligible_recontact_at` on `mip.gold.borrower_360`. Direct SQL: `marketing_eligible=true` for **233,420** rows / `false` for **4,922,764** rows. `consent_status` distribution: opt_in **2,001,241**, opt_out **183,671**, unknown **2,971,272**. `suppression_reason`: do_not_contact **183,671**, recent_contact_cap **182,002**. Live `/api/portfolio/preview` (default) returns `marketable_population=233,420`; `criteria.marketing_eligibility='Any'` returns **5,156,184**. `/api/leads` default rows all carry `marketing_eligible=true, consent_status='opt_in', suppression_reason=null` + new `last_touch_at` / `eligible_recontact_at`. `/api/borrowers/B-102FL7THC6Q3L` carries the same fields. |
| P0-M2 Draft ignored consent/frequency cap | Fail-closed before composing | ✅ Verified. B-0FSL4B96HG6V4 (marketing_eligible=false, last_touch=2026-05-01, eligible_recontact_at=2026-05-31). `POST /api/outreach/draft {channel:'email'}` → **HTTP 409** with body `{"detail":"borrower hit frequency cap; earliest re-contact 2026-05-31"}`. Eligible B-102FL7THC6Q3L returns 200 with full body. |
| P0-M3 Disclosure footer was placeholder | Versioned tenant disclosures resolve per state/channel | ✅ Verified. Email body now ends "Summit Mortgage, NMLS #123456. Equal Housing Lender. This is not a commitment to lend. Terms subject to credit, collateral, and underwriting approval. To opt out of marketing, reply unsubs..." Response includes `disclosure_version` and `disclosure_state` (e.g., `summit-demo-2026-05-v1`, `_ALL`). SMS body: "Summit: mortgage review. Reply YES. Summit Mortgage NMLS #123456. Equal Housing Lender. Reply STOP to opt out. Msg and data rates may apply." Direct mail body has no "Reply" framing and a mail-appropriate opt-out path. |
| P1-M4 No consent/suppression/recency filter UI | Filters exposed on Segments / Lead Queue / Portfolio Builder | ✅ Verified visually. Segment Intelligence now shows **CONTACTABILITY Eligible only / CONSENT Any / RECENCY Any** chips after the existing LOCATION/OCCUPANCY/LIEN/OWNER LINK/PURCHASE INTENT/CASH-OUT. Lead Queue queue-filter card mirrors the same three chips after STATE/RELATIONSHIP/SEGMENT/PRODUCT. With CONTACTABILITY=Eligible only the In-the-Money segment count drops from 134,534 → **6,204**. |
| P1-M5 No message variants | `message_variants` accepted on campaign create with PII-safe validation | ✅ Verified. The Maya-QA seeded campaign carries an array of `message_variants` with `variant_name / subject / body / channel / weight_pct`. Variant_name validation rejects PII shapes — `Call 212-555-1212` → **HTTP 422** "variant_name cannot contain PII, raw identifiers, or unresolved placeholders"; `Jane Smith` → **HTTP 422** "variant_name cannot contain human-name-shaped text"; `jane@example.com` → **HTTP 422** same as phone. |
| P1-M6 No list/get/patch for campaigns | `/api/portfolio` + `/api/campaigns` list, owner/admin-scoped, status advancement audited | ✅ Verified. `GET /api/portfolio` and `GET /api/campaigns` both return 200 with the same list. Seeded campaign `04da0c51-e10e-4a12-a320-ab1250c93254` "Maya QA CA recapture 9a8e2a" carries `status=pending_review`, full `criteria`, `suppression_policy {frequency_cap_days, marketing_eligibility, exclude_suppression_reasons}`, and `message_variants`. |
| P1-M7 `marketing_eligible` in CTAS but not in live gold | Gold projects the column | ✅ Verified. `DESCRIBE mip.gold.borrower_360` now includes the column; counts match `/api/portfolio/preview`. |
| P1-M8 Single disclosure constant | State/channel keyed disclosures with _ALL fallback | ✅ Verified by `disclosure_state` and `disclosure_version` on every draft response (e.g., `disclosure_state="_ALL"`). |
| P1-M9 External deliverability/attribution | Deferred to post-Module-0 send-integration tranche | 🟡 Correctly deferred — no external send path; no callback in scope. |
| P2-M10 Send-window scheduling | Validated send_window metadata | ✅ Verified by inspection of the seeded campaign criteria (`days / local_start / local_end / timezone`). The API enforces HH:MM format on local times (my probe sloppily used a wrong shape; the server correctly 422'd). |
| P2-M11 Channel cascade | Validated channel_cascade metadata | ✅ Verified. Cascade requires ordered step objects with channel + gates (my probe used bare strings and was correctly 422'd). |
| P2-M12 Holdout/control group | hash_modulo holdout config | ✅ Schema present (Maya-QA campaign carries the holdout block per the source). |
| P2-M13 Direct-mail channel missing | Outreach draft supports email/sms/direct_mail | ✅ Verified. `channel='direct_mail'` returns a print-formatted body with no "Reply YES" / "Reply STOP" SMS phrasing, plus the same disclosure footer keyed to state. |
| P2-M14 ROI assumptions missing | Validated budget/conversion/LO capacity/per-channel cost | ✅ Schema present (the campaign-create body accepts and persists these fields). |
| P2-M15 Saved-campaign templates | Saved campaigns reusable as starting point; full template library deferred | 🟡 Correctly partial. List works; clone-as-template would be the next step. |
| P2-M16 Fair-lending copy review | Deferred to fair-lending tranche (paired with V-18) | 🟡 Correctly deferred. Drafts already block PII/placeholders + require governed disclosures, so the worst-case scrub is contained. |

**Genie proof-layout regression check:** asked "How many borrowers are in the in-the-money segment?" → answer **"There are 134,534 borrowers currently in-the-money. This is a unique borrower count from mip.gold.borrower_360 at the gold borrower grain, so multi-segment borrowers are counted once."** Source: `trusted_sql`, `metric_value=134,534`, `proof.trusted=true`, full proof object with separated `sql_query / source_assets / data_freshness / row_count / filters / reasoning_trace / known_data_gaps / conversation_id / message_id / elapsed_ms / generated_at`. No regression.

**HoG + VP no-regression sweep against this deployment — all green:**
- HoG P1-G3 unknown POST → **422**; P1-G5 PII portfolio name → **422**; P2-G5 ZIP legacy → **422**, canonical → **200**.
- VP P0-V2 legacy admin PUT → **410**; P0-V1 reject without `rationale_code` → **422**, with PII-shaped `request_id` → **422**; P1-V9 borrower search → **200**; P1-V10 audit actor filter → **200**; P2-V17 weekly rollups → **200**.
- Home regression: `criteria.marketing_eligibility='Any'` returns **5,156,184** as expected for Pat's headline cards.
- Admin rules version `itm_4df231d5472f` unchanged.
- Health: `status=ok`, all three circuit breakers `closed`.

**Net:** Maya's persona-walk now passes end-to-end. The campaign **design** workflow and the campaign **execution-safety** layer (consent, suppression, recency, disclosure) are both wired and verifiable. External send integrations and post-send attribution remain correctly outside Module 0 scope, paired with the V-18 fair-lending tranche for the copy-lint work.
