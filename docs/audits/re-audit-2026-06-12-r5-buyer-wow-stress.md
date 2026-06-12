# Module 0 — Re-Audit #5: Buyer-Wow Stress Test (live, adversarial)

> **Internal validation artifact — not approved for public release.**
> Scope: break-testing of the shipped buyer-wow features against the deployed app
> (overnight session, Jun 12 ~2:30 AM EDT; app refreshed Jun 12 1:59 AM EDT; HEAD `129ab34`),
> plus code review of merges `87d75fa`/`d441711`/`b74ac7f`/`6795263`. No code modified.

## Headline corrections to the signoff framing

1. **It's 9 of 10, not 10 of 10.** Kiosk mode (#10 in my annex; "#9" in the agent's renumbering) was explicitly deferred post-Summit in r4 and has zero code. The signoff only claims four features this turn — accurate — but the "all 10 implemented" framing upstream is wrong by one.
2. Pins persist to **localStorage only** (`mip.pinnedInsights`, actor-scoped, max 6) — personal bookmarks, no Lakebase row, no audit event. Defensible design, but **pins will not roam to the booth machine**: whatever you pin while rehearsing on this laptop won't be there on the demo box. Pin live, on the machine you present from.
3. The pin gate is a **denylist (default-allow)**: `degraded/policy_blocked/refused/data_gap/out_of_footprint` are blocked; anything else — including future backend source values — is silently pinnable. Tests pin the boundary both directions today; a shared enum or backend-parity test would make it durable.

## Live stress results (zero console errors across the entire session)

| # | Feature | Live result |
|---|---|---|
| 1 | ⌘K palette | **PASS.** Open → "60617" → grouped borrower results → ↓ + Enter navigated to the *second* result's dossier (selection state correct). Focus lands in input; esc/↵/↑↓ hints rendered. Code: focus trap, abortable debounced search, once-bound listener. |
| 2 | KPI entrance | PASS (code + visual). One-time entrance via `useFirstAppearance`; **nit:** animation key includes the value, so a live data refresh re-animates the number once — visible if a refresh lands mid-walkthrough. `useCountUp.ts` is dead code — delete. |
| 3 | Tell the story | **PASS — the strongest feature.** Story card on Borrower 360 renders plain-English narrative with per-claim verified chips. Cross-checked every figure against the dossier on B-1PF07VAMZVFTY: 25 properties ✓, 12.50% rate ✓, 598 bps = 12.50 − 6.52 ✓, 81% equity = 100 − 19 LTV ✓, $39K lien ✓ — "Every figure verified against the source dossier." Deterministic (no LLM), missing-field fallbacks handled. Note: per-claim verifier is largely tautological (verifies the number against itself); the count-based prose scan is the real tripwire. |
| 4 | Geo transition + ZIP settle | PASS (prior-round drill + code). CSS-only, reduced-motion safe, hover-lift clobber fixed pre-merge; out-of-footprint click is a no-op by design (the live spec originally clicked Alaska — now drills a discovered in-footprint state). Not re-driven ×N this session (context cap) — covered by `buyer_wow_live.spec.ts` 4/4. |
| 5 | Pipeline funnel (Sankey) | **PASS with a data-shape oddity.** Renders beautifully; "Exact figures" panel beneath. But the ribbon **balloons mid-funnel: High Opportunity 3.88K → Offer Recommended 4.47M** (different denominators: offers span the scored universe, not the high-opp subset), and post-purge "Approved 8 · 0.0%" reads as a flatline. A buyer *will* ask "how do 3.9K become 4.5M?" — either branch offers in parallel from In-the-Money, or relabel stages with their true denominators before Sunday. |
| 6 | Morning briefing | **PASS — honest to a fault.** "1 metric moved vs 2026-06-02 — Approved outreach down 74.2%," with a footnote: *"Material step change on 2026-06-11; verify rules or refresh context before presenting this as market movement"* (the purge, correctly not dressed up as market signal) and a comparison-window caveat. Demo note: the headline will lead with that purge artifact until approvals re-accumulate — seed a few approvals before the booth or it opens on "down 74.2%." Nit (code): null `deltaPct` mover renders "up 0.0%". |
| 7 | ROI projector | PASS (code-verified validation: NaN/negative/>100% → "—"; no upper money bound — `$1000000.0B` cosmetic). Not re-driven this session. |
| 8 | Evidence hover | PARTIAL-PASS. Component healthy (portal, timer cleanup, scroll/resize hide); my hover on a Supporting-evidence chip produced no card at 1s — attach-point coverage may be uneven across chip types. The "stuck card" suspicion was a false alarm (a permanently-mounted, opacity-0 **glossary tooltip** — which is itself a nice undocumented touch). Worth one manual hover pass over each chip family before the demo. |
| 9 | Genie follow-ups + Pin-to-Home | **PASS end-to-end, including the boundary.** Trusted answer → ASK follow-up chips + "Pin to Home" → click → "Pinned to Home", localStorage written → Home renders PINNED INSIGHTS card with question/summary/source chip → unpin ✕ → card gone, storage `[]`. Refusal probe ("average borrower age in Illinois") → governed fair-lending refusal, "review" proof chip, **no pin button** ✓. Two defects: **(a)** the refusal still renders a fallback follow-up chip ("Which segments drive this?" — "this" being a refusal); gate `effectiveFollowUps` on the same trust boundary. **(b)** the pinned card shows **raw markdown** ("Illinois (\*\*IL\*\*) … with \*\*55,037\*\*") and the 220-char summary truncates mid-token, leaving a dangling "(**" on the Home hero. Strip markdown + truncate at a word boundary. |
| 10 | Kiosk | **NOT IMPLEMENTED** (deferred post-Summit, correctly tracked). |

**Repetition coverage:** palette opened 2× with distinct queries + keyboard nav; Home loaded 4× (briefing/KPI/pins); story figures verified on 2 borrowers across rounds (plus r4's three trio dossiers); Genie exercised 2× this session (1 trusted ~28 s, 1 refusal ~5 s) on top of 4 prior-round calls; pin lifecycle exercised full-cycle once + storage-verified; approve/draft, search, theme, hotkey flows carried over from r4 within the same 24 h. Geo/ROI/Sankey-hover received single-pass or code-level coverage this round — the gated live spec (4/4) and prior rounds carry them.

## Defect ledger (all P3, none blocking)

1. Pinned-card raw markdown + mid-token truncation (Home hero, demo-visible) — fix before Sunday.
2. Refusal answers offer fallback follow-up chips (trust-gate `effectiveFollowUps`).
3. Sankey mid-funnel balloon + "0.0%" stage labels (data-shape, demo-narrative).
4. Briefing headline currently leads with the purge artifact (operational: re-seed approvals pre-booth).
5. KPI re-animates on mid-session data refresh (key includes value).
6. Dead `useCountUp.ts`; `morningBriefing.ts:84` "up 0.0%" on null delta; no upper bound on ROI money inputs.
7. Pin denylist is default-allow (future sources auto-pinnable); localStorage pins don't roam devices.
8. Evidence hover attach-coverage uneven across chip families (one manual pass recommended).

## Bottom line

The four new features are real, live, and demo-grade; #3 ("Tell the story") is the single best moment in the product and survived figure-by-figure adversarial checking. The boundary the architect blocked on (#9 pin gating) holds in both directions live. Nothing found tonight blocks the demo; items 1–4 above are the polish slice I'd ship before Sunday, in that order.
