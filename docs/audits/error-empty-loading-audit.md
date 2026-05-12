# Error / empty / loading state audit

> **Internal validation artifact — not approved for public release.** Live behavioral pass against the deployed app, forcing every API surface into its three non-happy states: empty result, slow inflight, and explicit error.

**Auditor:** Claude (Cowork)
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`, deployment `01f14e554fcd12a9bfc8eab46332c320`
**Method:** Claude-in-Chrome MCP at 1440×900, with two test tools — (1) deep-link URLs that force empty filter combinations or invalid parameters, and (2) JavaScript fetch interception to slow specific routes (`/api/leads` throttled to 8s).
**Scope:** Lead Queue, Borrower 360, Ask Genie, Admin Audit Explorer, global topbar search. Plus inline state on the Sales-ops snapshot, KPI cards, and data-estate panel.

---

## Headline result

The product handles **most** non-happy paths well: empty queries return clean "No matches" copy, search dropdown narrates the miss in context, the Lead Queue retains its filter chips so the user understands what state produced the empty result. **Two real defects** found — one in error-class labeling (404 reported as "Backend unavailable") and one in raw HTTP error surfacing ("Couldn't load leads: 422" instead of a human-readable message). **Three quality issues** around loading-state legibility, audit-explorer filter behavior, and missing skeleton placeholders during slow API calls.

---

## Per-surface matrix

| Surface | Empty | Loading | Error (4xx) | Error (5xx) |
|---|---|---|---|---|
| Lead Queue | ✅ "No leads match this filter." + filter chips retained + footer "Showing 0 of 0 total matching filters" | 🟡 "Loading leads…" plain-text only (no spinner, no skeleton rows) | 🔴 Raw "Couldn't load leads: 422" banner + Retry button (Retry won't help an invalid filter) | (not forced — Lakebase healthy) |
| Borrower 360 | n/a (every URL produces either a real borrower or 404) | ✅ Skeleton placeholders (`kpi__value-skeleton` etc.) for ~2s | 🔴 **404 labeled "× Backend unavailable" pill** — should distinguish "not found" from "backend down" | (not forced) |
| Search dropdown | ✅ "No borrower, ZIP, city, county, or state matches 'ZZZZZZZ'." — clean, specific, scope-aware | ✅ (results appear inline as user types; no separate loading state needed for client-side narrow search) | n/a | n/a |
| Audit Explorer (Admin) | 🟡 Filter inputs don't appear to apply — typing `B-DOES-NOT-EXIST-EVER` + Enter clears the input and leaves the global last-25 list unchanged. No "filter applied" indicator. | n/a (rollup data renders fast) | n/a (no surface to error) | n/a |
| AI data estate panel (Home) | n/a (always has lane data) | 🟡 No skeleton on the lane cards during slow load — they just appear late | n/a | n/a |
| KPI cards (Home) | ✅ Skeleton placeholders (verified earlier in button audit) | ✅ Same | n/a | n/a |
| Sales-ops snapshot (Lead Queue) | ✅ Shows 0s gracefully ("0 reached · 0 callbacks · 0 apps. / No LO dispositions logged this week.") | ✅ Renders independently of `/api/leads` so the panel survives a slow leads request | n/a | n/a |
| Ask Genie | n/a | ✅ **Excellent**: "Opening a governed Genie turn" + animated progress bars + "Live Genie calls can take 10–20 seconds while SQL compiles and runs" copy | (rare — verified in earlier persona audits) | (rare) |

---

## Defects

### 🔴 Defect 1 — Borrower 360 404 is labeled "Backend unavailable"

**Reproduction:** navigate to `/borrower-360/B-DOES-NOT-EXIST`. The page shows:

- Header: "Couldn't load B-DOES-NOT-EXIST"
- Sub-copy: "Couldn't load borrower B-DOES-NOT-EXIST: 404"
- **Red pill: `× Backend unavailable`** ❌ — incorrect class label
- Buttons: Retry / Back to lead queue

A 404 means the backend processed the request fine and the resource doesn't exist (user mistyped, lead was rejected, ID is stale). A 5xx means the backend itself is unavailable. Labeling 404 as "Backend unavailable" conflates two very different failure modes, and the **Retry** button is the wrong CTA — retrying a 404 keeps 404-ing.

**Fix:**
- 404 path: "Borrower B-X not found. Check the ID, return to the lead queue, or use search above." CTA: Back to lead queue. No Retry button.
- 5xx path: keep current copy. CTA: Retry + Back to lead queue.

Code ref: `frontend/src/routes/borrower-360.tsx` (the error-state component is rendered when `api.borrower(id)` rejects; the error class needs to branch on `err.status === 404`).

### 🔴 Defect 2 — Lead Queue 422 error surfaces the raw HTTP code

**Reproduction:** navigate to `/lead-queue?approval_status=hold&aged_days=999`. The API returns 422 (validation error, likely `aged_days` exceeds the bounded max). The page shows:

- Active filter pills: `approval = hold` + `aged > 999d` (✓ honest)
- Filter chips: APPROVAL: Hold + AGING: Aged >999d (✓ URL-driven)
- **Red banner: "Couldn't load leads: 422"** ❌ — the user sees an HTTP code with no context
- "Retry" button (won't help — the input is the problem)
- Empty table

A 422 means "your inputs are invalid." The error should:
1. Surface the *which input* is invalid, not just "422" (the response body has this — `loc: ["query","aged_days"]`).
2. Suggest a Clear filters / Pick a valid range CTA, not Retry.

**Fix:** parse the 422 response body's `detail[]` and show the offending field + the validation message. Replace Retry with "Clear filters" when the error is 422-class.

Code ref: `frontend/src/routes/lead-queue.tsx` (the leads-fetch error handler; today it stringifies status code instead of inspecting the detail).

---

## Quality issues

### 🟡 Quality 1 — "Loading leads…" is plain text, no spinner or skeleton

When `/api/leads` is slow (8s+), the Lead Queue shows the text `Loading leads…` above the table body but offers no visual differentiation from an empty state. A user mid-load might think they've hit a 0-result filter and start re-clicking other filters.

**Fix:** render skeleton table rows (matching the column count) during inflight, or at minimum add a spinner glyph next to the "Loading leads…" text. Same pattern the KPI cards already use.

### 🟡 Quality 2 — AI data estate panel has no skeleton during slow load

The KPI cards above it skeleton-shimmer correctly. The data-estate panel below just appears late with no placeholder. For Pat presenting from this page, a 4-second gap with nothing in that area looks like a half-broken render.

**Fix:** add the same skeleton treatment to each lane card (lane title bar + 5 placeholder rows).

### 🟡 Quality 3 — Audit Explorer filter inputs don't appear to apply

Typing `B-DOES-NOT-EXIST-EVER` into the ENTITY ID input and pressing Enter:
- The input is cleared (visible from screenshot — empty placeholder returns).
- The rows below remain the global last-25 events.
- "Approval status by week" rollup is unchanged.

There's no submit button, no "filter applied" pill, no "0 matches" feedback. A user can't tell whether the filter ran or not. If filters auto-apply on type/blur/enter, the contract isn't visible.

**Fix:** add an explicit Apply / Filter button OR add a "Filtered to ENTITY_ID=X" pill that persists after the input clears, so the operator knows the filter is live. Best: keep the input value persisted in the field, show a "Filtered: 0 matches" callout, and provide a Clear filters affordance.

### 🟡 Quality 4 — Borrower 360 error page has no chrome (no left rail visible)

When `/borrower-360/B-DOES-NOT-EXIST` errors, the page strips most of the chrome — only the topbar + minimal footer remain. The M0–M4 left rail and bottom nav are gone. Whether that's intentional (minimize distraction from the error) or a layout collapse is unclear. For an operator hitting a stale link from a Slack notification, having the global nav available to click out to a real surface is helpful.

**Fix:** preserve the AppShell chrome on error states. The 404 panel renders inside `<main>` instead of replacing the full layout.

---

## What works well

- **Empty state on Lead Queue**: filter chips retained as breadcrumb, "No leads match this filter." copy is clear, count footer "Showing 0 ranked borrowers of 0 total matching filters" is unambiguous.
- **Search empty**: "No borrower, ZIP, city, county, or state matches 'ZZZZZZZ'." — scope-aware copy.
- **Ask Genie loading**: best-in-class. The "Opening a governed Genie turn" + progress bar + 10–20s expectation-setting copy is exactly what you want when the user is staring at a 12-second SQL compile.
- **KPI card skeletons**: P0-G1 fix held. No em-dash placeholders during cold-load.
- **Sales-ops snapshot resilience**: renders independently of `/api/leads`, so even when the table errors the snapshot is informative.
- **Filter pills retained** across empty/error states: the user always knows what state produced the result.

---

## Summary verdict

- **8 surfaces tested across 3 non-happy states** (= 24 cells in the matrix, of which several are n/a).
- **2 real defects** (Borrower 360 404 mislabeling, Lead Queue 422 raw status).
- **4 quality issues** (no spinner on Lead Queue load, no skeleton on data-estate panel, audit-explorer filter behavior unclear, error page strips chrome).
- **6 surfaces working well** as documented.

Both defects are 1–2 line fixes (branch error class on `status === 404`; parse 422 detail body and switch Retry → Clear filters). The quality issues are P2 polish — none blocks production but they raise the floor of the experience.

---

## Sources

- Live deployment `01f14e554fcd12a9bfc8eab46332c320`
- Forced fetch throttling via `window.fetch` interception (`leads_fetch_throttled_8s`)
- Deep-link URLs: `/lead-queue?states=NY`, `/borrower-360/B-DOES-NOT-EXIST`, `/lead-queue?approval_status=hold&aged_days=999`, search input `ZZZZZZZ`, audit-explorer ENTITY_ID `B-DOES-NOT-EXIST-EVER`
- Cross-checked HTTP status codes via direct `curl` calls earlier in this audit run
