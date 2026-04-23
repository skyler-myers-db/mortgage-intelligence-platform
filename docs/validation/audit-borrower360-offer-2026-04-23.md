# Audit — Borrower 360 + Offer Orchestrator (2026-04-23)

Scope: `frontend/src/routes/borrower-360.tsx`, `frontend/src/routes/offer-orchestrator.tsx`, and the components they render (`TriggerTimeline`, `EvidenceChip`, `ScoreBadge`, `ConfidenceMeter`, `ApprovalBanner`, `Skeleton`).

Wiring key:
- **LIVE** — bound to backend payload, click does real work.
- **STUB** — hardcoded value or decorative element, no binding.
- **NAV** — navigation only (router link / redirect).
- **BROKEN** — wired to something that does not do what the UI implies.
- **DEAD** — no handler, no action.

## 1. Element × wiring table

### Borrower 360 (`frontend/src/routes/borrower-360.tsx`)

| Route | Element | Action | Wired? | Evidence |
| --- | --- | --- | --- | --- |
| /borrower-360 (no id) | `<Navigate to="/lead-queue">` | Redirect when id missing | LIVE | borrower-360.tsx:57 |
| /borrower-360/:id | `api.borrower(id)` fetch | GET /api/borrowers/:id | LIVE | borrower-360.tsx:35 → api.ts:197 → backend/api/borrowers.py:47 |
| Error state | "Back to lead queue" `<Link>` | Router nav | NAV | borrower-360.tsx:70-72 |
| Hero | `<ScoreBadge value={b.opportunity_score}>` | Render from payload | LIVE | borrower-360.tsx:154 |
| Hero | `<ConfidenceMeter value={b.confidence}>` | Render from payload | LIVE | borrower-360.tsx:155 |
| Hero | `<Chip variant="warning">Approval pending</Chip>` | Decorative chip — never reflects real approval state | STUB | borrower-360.tsx:156 — always renders `Approval pending` even after approve via offer orchestrator (`approvals[id]` not checked here) |
| Customer 360 | `b.clip_id`, `b.owner_link_id`, AVM, lien, LTV, equity | Render from payload | LIVE | borrower-360.tsx:181-198 |
| Customer 360 | Property address | Strips "Synthetic property · " prefix before render | LIVE | borrower-360.tsx:143-145 (defensive clean; still real backend field) |
| Customer 360 | Related properties count | Render from payload | LIVE | borrower-360.tsx:198 |
| Customer 360 | Segment chips | Rendered from `b.segment_codes` via `segmentByCode` | LIVE | borrower-360.tsx:204-228 |
| Trigger timeline | `<TriggerTimeline events={b.trigger_timeline}>` | Iterates real backend events | LIVE | borrower-360.tsx:242 → TriggerTimeline.tsx:37 |
| Trigger timeline row | `e.timestamp`, `e.display_text`, `e.source_product`, `e.signal_value` | From `EvidenceEvent` payload | LIVE | TriggerTimeline.tsx:39-42 |
| Trigger timeline row | Evidence chip | **BEFORE:** always routed to `DRAWER_SOURCES.itm` regardless of source_table. **AFTER (fixed inline):** routed via `descriptorFor(e.source_table)` → the right drawer entry opens per event. | LIVE (post-fix) | TriggerTimeline.tsx:43 |
| Why panel | In-the-money chip `+{bps} bps vs. par {market_rate}%` | From `b.why_panel` | LIVE | borrower-360.tsx:257-265 |
| Why panel | Rationale narrative | From `b.why_panel.in_the_money_reason` (backend copy) | LIVE | borrower-360.tsx:267-270 |
| Why panel | Evidence chips | **BEFORE:** every chip routed to `DRAWER_SOURCES.itm` + two hardcoded extras ("Next-best-offer model", "Building permit signal") that appeared on every borrower regardless of whether permit fired. **AFTER (fixed inline):** chips routed via `descriptorFor(s)` per source; hardcoded extras removed. | LIVE (post-fix) | borrower-360.tsx:273-286 (previous) |
| Next-best-offer | `b.recommended_offer`, `b.why_now` | From payload | LIVE | borrower-360.tsx:298-301 |
| Next-best-offer | "Build outreach draft" `<Link to="/offer-orchestrator/:id">` | Router nav | NAV | borrower-360.tsx:303 |
| Supporting evidence | List of `b.evidence_events` | Render from payload | LIVE | borrower-360.tsx:317-322 |
| Supporting evidence | Per-event evidence chip | **BEFORE:** hardcoded to `DRAWER_SOURCES.itm`. **AFTER (fixed inline):** routed via `descriptorFor(e.source_table)`. | LIVE (post-fix) | borrower-360.tsx:319 |

### Offer Orchestrator (`frontend/src/routes/offer-orchestrator.tsx`)

| Route | Element | Action | Wired? | Evidence |
| --- | --- | --- | --- | --- |
| /offer-orchestrator (no id) | `<Navigate to="/lead-queue">` | Redirect when id missing | LIVE | offer-orchestrator.tsx:~99 |
| /offer-orchestrator/:id | `Promise.all([api.borrower, api.recommendOffer])` | GET /api/borrowers/:id + POST /api/offers/recommend | LIVE | offer-orchestrator.tsx:~77 → api.ts:197-203 → backend/api/borrowers.py + offers.py |
| Error state | "Back to lead queue" `<Link>` | Router nav | NAV | offer-orchestrator.tsx:152-154 |
| Hero | `<ScoreBadge value={b.opportunity_score}>` | From payload | LIVE | offer-orchestrator.tsx:169 |
| Hero | `<ConfidenceMeter value={b.confidence}>` | From payload | LIVE | offer-orchestrator.tsx:170 |
| Hero | `<Button variant="primary" ...>Approve</Button>` | Calls `onApprove` → POST /api/outreach/approve | LIVE | offer-orchestrator.tsx:~174-188 + onApprove |
| Hero | Approve disabled state | Disabled when `!rec` OR already approved | LIVE | offer-orchestrator.tsx:179 |
| Primary offer | `productLabel` | From `rec.product_label` (fallback `b.recommended_offer`) | LIVE | offer-orchestrator.tsx:108 |
| Primary offer | Rationale text | From `rec.rationale` (fallback `b.why_now`) | LIVE | offer-orchestrator.tsx:207-208 |
| Primary offer | Source chips | `rec.source_labels[idx].display_label` with fallback to short UC; clicking opens drawer via `descriptorFor(s)` | LIVE | offer-orchestrator.tsx:219-235 |
| Draft outreach | `<textarea>` with `defaultValue={defaultDraft}` | **Hardcoded JSX template literal in component.** Backend has `/api/outreach/draft` that returns real subject + body; UI never calls it. | STUB (hardcoded template) | offer-orchestrator.tsx:109-117 (template), 245-262 (textarea). Backend endpoint exists at backend/api/outreach.py:58-97 but is unreached by this route. |
| Draft outreach | Textarea input | `defaultValue` only — edits are lost on re-render; no state binding; nothing is submitted anywhere on approve. | BROKEN | offer-orchestrator.tsx:245-262 — `defaultValue` (uncontrolled, no onChange, no ref, no submit). |
| Draft outreach | "Email channel" chip | Decorative — no channel toggle, backend `OutreachDraftRequest.channel` literal union never surfaced in UI | STUB (decorative) | offer-orchestrator.tsx:267 |
| Draft outreach | "LO call follow-up within 5 days" chip | Decorative — pure label | STUB (decorative) | offer-orchestrator.tsx:268 |
| Considered alternatives | `rec.alternatives` list | Render from payload | LIVE | offer-orchestrator.tsx:285-313 |
| Considered alternatives | `offer_code` chip per alt | From `alt.offer_code` | LIVE | offer-orchestrator.tsx:308 |
| Thresholds applied | `rec.thresholds_applied` entries | Render from payload; keys humanized via `THRESHOLD_LABELS` | LIVE | offer-orchestrator.tsx:41-47, 326-342 |
| Approval banner | Approve button | Calls `onApprove` → POST /api/outreach/approve | LIVE | offer-orchestrator.tsx:354, ApprovalBanner.tsx:40 |
| Approval banner | Reject button | Calls `onReject` → updates `approvals[id] = 'rejected'` in AppContext **and does NOTHING on the backend**. No audit row written. No `/api/outreach/reject` endpoint exists. | BROKEN (audit) / STUB (local only) | offer-orchestrator.tsx:~138-140 (onReject); backend has no reject route. |
| After approve | "audit: {auditId}" display | Real UUID from `OutreachApproveResponse.audit_event_id` via `backend/services/audit_store.AuditStore.write()` → Lakebase `mip_app.action_audit` | LIVE | offer-orchestrator.tsx:366, backend/api/outreach.py:136-148 |
| After approve | "Approved · released to outreach queue" | Local-state chip after successful approval | LIVE | offer-orchestrator.tsx:360-368 |
| After reject | "Rejected" chip | Local-state chip; no backend record | STUB | offer-orchestrator.tsx:370-376 |
| Approve error surface | Red banner | Renders `approveError` from thrown exception or `approved=false` branch | LIVE | offer-orchestrator.tsx:377-387 |

## 2. Fixed inline

All fixes are small, frontend-only, and run green on `npm --prefix frontend run lint && npm --prefix frontend run build`.

### a) Route evidence chips to the correct drawer source (not always `itm`)

Added a shared helper `descriptorFor(rawSource)` in `frontend/src/lib/drawerSources.ts` that routes a UC source (`mip.gold.fn_in_the_money`, `cotality.permits.building`, etc.) to the matching `DRAWER_SOURCES` entry with a neutral fallback. The helper is the single truth; two places now use it.

```ts
// frontend/src/lib/drawerSources.ts (new export)
export function descriptorFor(rawSource: string): DrawerSource {
  const key = rawSource.toLowerCase();
  if (key.includes('fn_in_the_money') || key.includes('itm') || key.includes('rate_spread')) {
    return DRAWER_SOURCES.itm;
  }
  if (key.includes('fn_next_best_offer') || key.includes('fn_lead_score') || key.includes('nbo')) {
    return DRAWER_SOURCES.nbo;
  }
  if (key.includes('permit')) return DRAWER_SOURCES.permit;
  if (key.includes('population') || key.includes('public_records')) return DRAWER_SOURCES.population;
  return {
    title: rawSource,
    short: rawSource.split('.').pop() ?? rawSource,
    description: `Unity Catalog object: ${rawSource}. Click through for lineage once wired.`,
    lineage: [{ layer: 'UC', name: rawSource }],
    signals: [],
  };
}
```

### b) `TriggerTimeline` — route chip per `e.source_table`

```diff
- import { DRAWER_SOURCES } from '../../lib/drawerSources';
+ import { descriptorFor } from '../../lib/drawerSources';
...
-            <EvidenceChip source={DRAWER_SOURCES.itm}>{e.source_table.split('.')[0]}</EvidenceChip>
+            <EvidenceChip source={descriptorFor(e.source_table)}>{e.source_table.split('.')[0]}</EvidenceChip>
```

### c) Borrower 360 — Why panel evidence chips + removed hardcoded tail chips

Every chip was routing to `DRAWER_SOURCES.itm` and two hardcoded extras ("Next-best-offer model", "Building permit signal") appeared on every borrower regardless of whether those signals were in `why_panel.sources`. Fixed:

```diff
-                    <EvidenceChip key={s} source={DRAWER_SOURCES.itm}>
+                    <EvidenceChip key={s} source={descriptorFor(s)}>
                       {label}
                     </EvidenceChip>
                   );
                 })}
-                <EvidenceChip source={DRAWER_SOURCES.nbo}>Next-best-offer model</EvidenceChip>
-                <EvidenceChip source={DRAWER_SOURCES.permit}>Building permit signal</EvidenceChip>
```

### d) Borrower 360 — Supporting evidence chips route per source table

```diff
-                  <EvidenceChip source={DRAWER_SOURCES.itm}>{e.source_product}</EvidenceChip>
+                  <EvidenceChip source={descriptorFor(e.source_table)}>{e.source_product}</EvidenceChip>
```

### e) Offer Orchestrator — approve forwards `offer_code` + `evidence_ids`

Previous behaviour: `api.approve(id)` sent only `{borrower_id, actor}`. The `OutreachApproveRequest` schema accepts `offer_code` and `evidence_ids` and the audit row in `backend/api/outreach.py:136-148` carries them — so the audit row was being written with **zero evidence ids** and **null offer_code**, losing the "who viewed what" fidelity governance §4 requires.

```diff
// frontend/src/lib/api.ts
-  approve: (borrower_id: string, actor = 'anonymous') =>
-    postJson<ApproveResult, { borrower_id: string; actor: string }>(
-      '/api/outreach/approve',
-      { borrower_id, actor },
-    ),
+  approve: (
+    borrower_id: string,
+    opts: { actor?: string; offer_code?: string | null; evidence_ids?: string[] } = {},
+  ) =>
+    postJson<ApproveResult, { borrower_id: string; actor: string; offer_code?: string | null; evidence_ids?: string[] }>(
+      '/api/outreach/approve',
+      {
+        borrower_id,
+        actor: opts.actor ?? 'anonymous',
+        offer_code: opts.offer_code ?? null,
+        evidence_ids: opts.evidence_ids ?? [],
+      },
+    ),
```

```diff
// frontend/src/routes/offer-orchestrator.tsx
-      const res = await api.approve(id);
+      const offer_code = rec?.offer_code ?? b?.recommended_offer ?? null;
+      const evidence_ids = rec?.evidence_ids ?? b?.evidence_ids ?? [];
+      const res = await api.approve(id, { offer_code, evidence_ids });
```

### f) Offer Orchestrator — consolidate `sourceDescriptor` local copy → shared helper

Deleted the local `sourceDescriptor` function and unused `DrawerSource` / `DRAWER_SOURCES` imports; now uses `descriptorFor` from `drawerSources.ts`. Single source of truth keeps the two routes consistent.

## 3. Needs main-agent attention

1. **`Approval pending` chip in Borrower 360 hero (line 156) is static.** After a user approves the same borrower on the offer orchestrator, navigating back to Borrower 360 still shows "Approval pending". Should check `useApp().approvals[b.borrower_id]` and render "Approved" / "Rejected" accordingly. Low risk, but presenters notice — user asked about fake-looking numbers.
2. **Draft outreach textarea is fully hardcoded client-side.** The JSX template literal in `offer-orchestrator.tsx:109-117` is NOT the backend's draft; `/api/outreach/draft` generates a real subject + body and emits a `DRAFT_OUTREACH` audit row. Swap the uncontrolled textarea for a controlled state that (a) calls `/api/outreach/draft` on load to populate and emit the audit row, (b) keeps the operator's edits in state, (c) includes `subject` + `body` on `/approve` so what was approved matches what was shown. The current surface is a live-demo trust-fall — the textarea happily accepts keystrokes and throws them away.
3. **Reject has no backend path.** `onReject` only updates local state; no audit row is written for the rejection decision. Governance §4 explicitly asks for both approve AND reject to be captured. Add `/api/outreach/reject` + `REJECT` audit event + optional row in `mip_app.approvals` with `action='reject'`. Until then, the "Reject" button is observable product security theatre — mark it as such in the talk track or gate it behind an admin flag.
4. **"Email channel" and "LO call follow-up within 5 days" chips are purely decorative.** Backend `OutreachDraftRequest.channel` already supports `"email" | "sms"`. If channel choice is a real feature, wire a toggle; if it isn't part of Module 0, keep the chip but rename to something like "Planned channel: email" to set expectations honestly.
5. **Evidence drawer lineage for non-top-level sources (fn_rate_spread, fn_lead_score) still uses the NBO entry.** The `descriptorFor` routing adds `fn_lead_score` → NBO and `fn_rate_spread` → ITM as reasonable defaults, but ideally those would get their own `DRAWER_SOURCES.lead_score` + `DRAWER_SOURCES.rate_spread` entries with real lineage. Not a blocker; the current routing is an honest improvement over the "everything is ITM" baseline.
6. **Audit fire-and-forget verified safe.** I confirmed `trigger_lifecycle_sync` in `backend/services/job_trigger.py` does NOT write any audit row — it only calls `workspace.jobs.run_now`. The audit row is written exactly once by `audit.write` in `backend/api/outreach.py:136-148`. So approving does not double-fire audit events. Per-approval result: 1 `APPROVE` row in `mip_app.action_audit`, 1 row in `mip_app.approvals`, and at most one `jobs.run_now` call (debounced to ≥60s window). No issue found.

## 4. Honest fake-data inventory

Files with explicit fake/hardcoded values that reach these two routes' rendered surface:

| file:line | What | Comes from data? |
| --- | --- | --- |
| `frontend/src/routes/offer-orchestrator.tsx:109-117` | Draft outreach template literal referencing `[first name]`, city/state, productLabel, `1-800-XXX-XXXX` | **No** — hardcoded in the component body. `productLabel` and `b.city/state` are real but the copy structure is client-side only. |
| `frontend/src/routes/offer-orchestrator.tsx:267-268` | "Email channel" and "LO call follow-up within 5 days" chips | **No** — decorative literals, no binding. |
| `frontend/src/routes/borrower-360.tsx:156` | "Approval pending" chip | **No** — always renders, even after a real approval flows through AppContext. |
| `frontend/src/components/mortgage/TriggerTimeline.tsx:43` | Evidence chip `.split('.')[0]` short label | Partial — `e.source_table` is real but the chip's **drawer target** was hardcoded (now fixed). |
| `frontend/src/lib/drawerSources.ts` (entire file) | Drawer copy, lineage metadata, signal values | **Intentionally static** — per the file's header comment, this is UI contract copy about UC objects, not borrower data. Not a bug. |

Real (backend-sourced) values in these two routes — verified:

- `borrower_id`, `clip_id`, `owner_link_id`, `avm_value`, `current_lien_balance`, `current_rate`, `ltv`, `equity_estimate`, `related_property_count`, `opportunity_score`, `confidence`, `segment_codes`, `trigger_timeline`, `evidence_events`, `why_panel.*`, `recommended_offer`, `why_now` — all from `GET /api/borrowers/:id`.
- `rec.offer_code`, `rec.product_label`, `rec.rationale`, `rec.sources`, `rec.source_labels`, `rec.alternatives`, `rec.thresholds_applied`, `rec.evidence_ids` — all from `POST /api/offers/recommend`.
- `audit_event_id` — real UUID from `backend/services/audit_store.AuditStore.write()` writing to `mip_app.action_audit` (Lakebase).

## 5. Validation

```
$ npm --prefix frontend run lint
> eslint . --max-warnings 0   # clean, no output

$ npm --prefix frontend run build
> tsc -b && vite build
vite v8.0.9 building client environment for production...
✓ 80 modules transformed.
✓ built in 113ms
```

Both pass. No commit made.
