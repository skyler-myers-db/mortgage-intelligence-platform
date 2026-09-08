# Genie Deep Research — Demo Talk Track

Companion to [module0-talk-track.md](module0-talk-track.md) (Beat 4). This
track is built around one capability: **Genie plans and executes its own
multi-part research sweep, live, over governed Unity Catalog assets, and
shows its work.** Every question below was run against the live app on
2026-09-08 and is quoted verbatim so the presenter can paste it. Do not
paraphrase on stage — see "Phrasing rules" for why.

## What "deep research" means in this product

When a question is inherently multi-part (a ranked shortlist, plus why each
one, plus the offer, plus how they compare with the population) or asks for
depth outright ("full analysis", "comprehensive", "thorough", "end-to-end"),
the app does not run one SQL statement. It asks the governed Genie space to
**plan its own decomposition** into 7–10 sub-questions, screens each planned
line with the same fair-lending / PII / scope guard battery a typed prompt
gets, runs every surviving sub-question as its own governed Genie turn in
parallel, verifies each narrative's numbers against its returned rows, and
then asks Genie for a closing synthesis that is itself verified against the
union of the rows. Nothing in the answer is authored server-side: the plan,
each section, the SQL, and the synthesis are all Genie's own work.

What the audience sees:

- The **progress rail** (Understand → Draft SQL → Execute → Format) and the
  public process steps while the sweep runs.
- The answer opens with **Summary** — Genie's verified executive synthesis —
  followed by **one titled section per finding** (titles are Genie's own,
  short, business-worded), each with Genie's narrative and **its own chart
  and table** built from that section's rows. No method preamble, no table
  names in the body; a muted source footnote closes each section.
- **Show proof** exposes the SQL for every section (labelled per section),
  the trusted assets cited, row counts, freshness, and any disclosed gaps
  (a planned line the guard screened, a section that timed out, a synthesis
  omitted because its numbers could not be verified, a first draft Genie
  rewrote from its verified rows).
- **Reasoning trace** lists the orchestration step and one "answered live
  over …" step per section.
- The route keeps the **whole thread**: every question and answer stays on
  screen, newest first, and the composer clears after each ask. History
  loads a past conversation; New thread starts a fresh one.

Timing to set expectations out loud: a single governed turn lands in about
20–25 seconds; a deep sweep lands in roughly 90–200 seconds because it runs
a planning turn, 7–10 parallel sub-turns (the deepest of which take 80–120
seconds), and a synthesis turn. The app caps the fan-out at 200 seconds and
ships whatever sections completed, disclosing the rest as gaps.

## Preflight (five minutes before)

1. `/api/v1/health` returns `status: ok` and `mode: live`; the topbar pill
   reads **Live**, not **Degraded**. Degraded with a `git_sha` missing means
   the app was deployed from the UI button without the operator payload —
   redeploy with `tools/databricks/app_deploy_payload.py` (see the runbook
   in the deploy memory); every async Genie turn 500s in that state.
2. The serverless warehouse `81d08d4fa2d799e9` is RUNNING. It auto-stops
   after **10 idle minutes**, and while it is stopped the health probe
   reports warehouse and Genie down and the pill reads **Degraded** (seen
   2026-09-08, twenty minutes after the last probe). Running Beat 0 five
   minutes before you start wakes it and the pill returns to Live within
   one health refresh; if the room runs long between questions, ask a
   single-turn question every few minutes to keep it warm.
3. Run the control question once (Beat 0). It warms the space and proves the
   round trip before anyone is watching.
4. Have the Ask Genie route open with an empty thread ("New thread"); a
   long transcript slows the page.
5. Run the deep questions **one at a time**. Two deep sweeps in flight at
   once is 16–20 concurrent Genie turns and will produce timeouts.

## The track

### Beat 0 — Control (single turn, ~20s)

> How many borrowers across the current Cotality data coverage are currently in-the-money, and what is the average rate spread?

Say: "One governed query, one number, one source line. Every deeper answer
you are about to see is built from turns exactly like this one."

Point at: the `Source: mip.gold.…` line, Show proof → SQL.

### Beat 1 — The executive's question (deep, the hero)

> Do a full analysis of the existing marketable population. What segments have the highest opportunity? What are the best moves for us as lenders? Who are the top candidates and why, and what offers should we be making exactly and why?

Why it routes deep: explicit "full analysis" plus four analytic parts.

While it runs (about two minutes): "Genie just wrote its own research plan
— a ranked shortlist with the signal columns behind the score, how that
shortlist compares with the whole marketable population, the offer mix and
the signals behind each offer, and at least one concentration angle a single
screen never shows. Each plan line is screened by the same fair-lending and
PII guards a typed prompt gets, then runs as its own governed SQL turn."

When it lands, walk top to bottom:

1. **Summary** first: Genie's own verified synthesis. Read two sentences
   aloud, then say "every number in that paragraph was checked against the
   rows below before it was allowed on screen."
2. The population sizing section, then the segment ranking section, each
   with its own chart.
3. The top-candidates section: masked `B-…` IDs only, scores, rate spread,
   equity, triggers, recommended offer. "No names, no addresses — the
   platform masks at the API, UI, export and audit boundary."
4. The comparison section: "why these" is provable — the top cohort's
   average spread and equity against the population's.
5. The offer-mix section, then any geography / co-occurrence section.
6. Back to the top: the Summary is the synthesis of everything below it.
   "Those sentences are Genie's. If a number in them could not be verified,
   Genie gets one rewrite from its own verified figures; if that fails too,
   the summary is withheld and the gap is disclosed."
7. Show proof: scroll the labelled SQL; count the sections; show the
   disclosed gaps if any.

Follow-up in the same thread (single turn): "Which five ZIP codes have the
most in-the-money borrowers?" — continuity, and a fast contrast.

### Beat 2 — Head of Growth: where to spend (deep)

> Give me a comprehensive analysis of our addressable market across the current Cotality coverage: how large is it, where is it concentrated by state, which segments carry the most opportunity, and what should we go after first and why?

Point at: state concentration, segment overlap (in-the-money × equity ×
investor × listed × retention), and the "prioritize first" synthesis.

### Beat 3 — VP Lending: the shortlist, defended (deep)

> Who are the top 15 borrower candidates across all segments overall, what makes each one such a strong candidate compared with the rest of the population, and which offer should we make to each and why?

Why it routes deep: shortlist + per-item rationale + offer call +
comparison. Point at: per-borrower "why now" language, the offer code per
row, the comparison against population averages.

### Beat 4 — Marketing Leader: the offer mix (deep)

> Do a deep dive on our recommended offer mix: how many borrowers fall under each offer code, what are the average rate spread and equity behind each, which states concentrate each offer, and where is the biggest untapped opportunity?

Point at: `recommended_offer_code` counts, the spread/equity behind each
offer (the instruction set pins refi at ≥ 75 bps, HELOC at ≥ 35% equity),
state concentration per offer.

### Beat 5 — The lock-in cohort (deep)

> Give me a thorough assessment of the rate lock-in cohort: how big is it, where is it concentrated by state, how much equity and HELOC potential does it carry, and what is the best offer for a lender since it will not rate-and-term refinance?

Say: "These borrowers hold 2020–2022 sub-3% notes. They will never
rate-and-term refi, so a refi campaign wastes money on them — but the
equity says HELOC. This is the insight a rate-spread dashboard cannot give
you." Source: `mip.gold.lockin_cohort` plus `borrower_360`.

### Beat 6 — Sales Manager: retention and recapture (deep)

> Do a full review of our current-customer retention opportunity: how many current customers are in the retention segment or carry a competitor lien, how do they compare with the rest of the book on rate spread and equity, and which offer should we recommend first and why?

Point at: `competitor_lien` evidence, the retention offer code, the
comparison with the rest of the book. Keep this wording: an earlier version
that said "retention and recapture **risk**" made the planner echo
"customers with retention or recapture risk" on every line, and the guard
refused eight of nine of them, so the sweep aborted to a single screen.

### Beat 7 — Geography as a hero surface (deep)

> Give me a comprehensive geographic read of the opportunity by state: which states concentrate in-the-money and HELOC-eligible borrowers, how do their average opportunity scores and rate spreads compare, and where should a lender put its next outreach touches and why?

Then the ZIP drill-down as a single-turn follow-up in the same thread:

> Which five ZIP codes have the most in-the-money borrowers?

Point at: ZIPs rendered as labels, never as numbers; then "Open this cohort
in Lead Queue" to show the answer becoming a governed workflow (preview the
filters, confirm, audited). Keep the deep question at state grain: the
ZIP-grain version scans every ZIP in every sub-analysis and ran to the
200-second budget with sections cut.

### Optional beats (all verified deep)

- Listed-for-sale / purchase play:
  > Do an end-to-end analysis of the listed-for-sale opportunity: how many borrowers are listed, how does it break down by state and loan product, how long have they been on market, and what does that mean for a purchase-mortgage play?
- Investor / Multi-Property:
  > Do a thorough analysis of the Investor / Multi-Property segment: how many borrowers it holds by state, their average current rate and equity, how many hold three or more properties, and which offer is strongest for that cohort and why.
- Funnel trend (uses the daily snapshots, not current rows):
  > Do a comprehensive review of the last 30 days of funnel snapshots: how did the lead population, approved borrowers, and outreach counts move, and which segments and states drove the change?

### Beat 8 — Governance, on purpose (instant)

> Show me the average lead score by borrower race.

Refused in under a second, before any query runs, with the fair-lending
explanation and the audit row (`genie.refused_prompt`). Then:

> Write me an email to send to the top 10 borrowers about a refinance offer.

Refused as a contact-data / outreach request and pointed at the Outreach
route. Say: "The research agent is powerful because it is bounded. It reads
only the trusted gold and semantic assets, it never sees names or
addresses, it cannot select on a protected class, and it cannot send
anything. Every answer and every refusal is audited."

## Persona map

| Persona | Beats | The insight they cannot get from a dashboard |
|---|---|---|
| Head of Growth | 1, 2, 7 | Where the next outreach touches go, with the comparison that proves "why these" |
| VP Mortgage Lending | 1, 3, 5 | A defended shortlist with the offer per borrower, and the cohort that must not get a refi pitch |
| Marketing Leader | 4, 7 | Offer mix with the signals behind each offer, by state |
| Sales Manager | 6, 3 | Recapture risk ranked against the rest of the book, with the first offer |

## Phrasing rules (read before ad-libbing)

Deep routing is triggered by an explicit depth phrase — *full / deep /
comprehensive / thorough / complete / in-depth / end-to-end* followed by
*analysis / review / dive / assessment / read / breakdown / investigation /
examination / audit / exploration* — or by two or more of: a ranked shortlist
("top", "rank", "best"), a per-item rationale ("why each", "what makes each
one"), an offer call ("which offer should we make", "best offer"), and a
comparison ("compared with", "relative to the rest of the book",
"across the whole portfolio"). If a question is meant to be deep and lacks
one of these, it runs as one governed turn and answers at the depth of one
screen.

Stay inside the reviewed vocabulary: opportunity / lead score, rate spread,
equity, in-the-money, HELOC, cash-out, refinance, listed for sale,
competitor lien, retention / recapture, current customers, investor /
multi-property, lock-in cohort, recommended offer, state / ZIP / county / MSA,
evidence and triggers, funnel snapshots. The fair-lending guard refuses any
criterion outside that set, and it is deliberately fail-closed.

Phrasings verified to be **refused as an unreviewed criterion** even though
they name none (they are filed as guard bugs; avoid them on stage):

- "what offer should we **lead with**" → say "which offer should we recommend first"
- "explain why each one is a strong candidate" after "top N borrowers by score" → say "what makes each one such a strong candidate"
- "customers **with retention signals**" → say "current customers in the retention segment" or "carry a retention signal"
- "the best move for a lender **given these borrowers will not** refi" → say "the best offer for a lender since it will not rate-and-term refinance"
- "how the funnel **moved** over the last 30 days across …" → say "how did the lead population, approved borrowers, and outreach counts move"

Do not ask for names, street addresses, raw CLIP / Owner Link values, or
any protected class. Do not ask about filed building permits (that source is
pending and the answer will be a disclosed data gap, correctly).

## Disclosures you will see, and what to say

These are the sweep working as designed, not faults. Each shows under
"known data gaps" in Show proof.

- **"One planned sub-analysis used selection vocabulary outside the
  reviewed set … and was not executed."** Genie's own plan line was
  screened by the same fair-lending guard a typed prompt gets. Say: "the
  guard screens the agent's plan, not just the user's question." (Most of
  these are guard false positives on ordinary analytics wording and are
  filed as bugs; the plan is over-sized so the sweep still ships.)
- **"A cross-section synthesis draft was omitted: it carried numbers the
  verified section results could not support."** The closing synthesis
  was withheld because Genie derived a figure the rows do not contain. Say:
  "every sentence Genie writes is checked against the rows it returned; a
  number it cannot prove does not ship." The sections stand on their own.
- **"Genie's first draft carried a figure the returned rows could not
  support; it rewrote the narrative from the verified figures and the
  rewrite passed verification."** The common case now: the prose you are
  reading is Genie's second draft, checked like the first.
- **"Genie's draft narrative included numeric or financial claims that
  could not be verified … the prose was withheld and the verified rows are
  shown."** The rewrite failed too: the section shows a plain digest of its
  rows with its chart and table, and no prose from the model.
- **"Genie's first draft used wording the output safety guard rejects; it
  rewrote the narrative from the verified figures and the rewrite passed
  the guard and verification."** The output guard is fail-closed and
  refuses contact and targeting vocabulary; Genie got one rewrite under the
  wording rule and it passed. Say nothing unless asked; if asked: "the same
  filter that blocks a request for names blocks outreach language in an
  answer, and the agent rewrites rather than argues."
- **"Governed cross-check: this answer's framing overlaps N of 10 borrowers
  with the canonical opportunity ranking; … the Lead Queue holds the
  operational list."** Genie ranked over the whole marketable population;
  the Lead Queue applies the operational filters (consent, contactability,
  suppression). Say it before someone compares the two screens.

## If something goes wrong on stage

- **A deep question lands as one short answer with no sections.** The plan
  did not survive its screen on that run (the planner rewords every run).
  Say "let me ask for the full analysis explicitly" and re-ask with
  "Do a full analysis:" in front. Do not re-ask while a sweep is still
  running. Operators can read why from the app log: `genie_sweep_plan`
  (planned vs floor), one `genie_sweep_section` per sub-turn (source, rows,
  prose), and `genie_sweep_result` (shipped or which gate aborted).
- **"Genie is taking longer than expected".** The client waited five
  minutes on progress. The turn usually completes server-side; ask again.
- **A gateway 504.** The sweep overran the 200-second budget on a slow
  warehouse; ask again — the space is now warm.
- **Degraded pill.** Check the warehouse first, then the deployment's env
  vars (a UI-button deploy strips them). Health at `/api/v1/health`
  says which.
- **A refusal on a reasonable question.** Reword with the reviewed
  vocabulary above; every refusal is a guard false positive to report, not
  a data limitation.

## Verified evidence (live app, 2026-09-08)

Sequential live probe against `mip-app` after the planner-cap fix shipped
(commit 72688648, PR #232). Elapsed is wall time from submit to answer;
"sub-analyses" is the number of governed sections that shipped; "synthesis"
is whether the verified closing synthesis shipped. Numbers drift with every
gold refresh — quote the shape, not the figures.

| Beat | Elapsed | Source | Sub-analyses | Synthesis | Assets cited |
|---|---|---|---|---|---|
| 0 Control (in-the-money count) | 24s | genie | single turn | — | lead_scores |
| 1 Hero: full analysis of the marketable population | 140s | genie | 6 of 8 planned | yes | segment_performance_metric_view, borrower_360 |
| 1 Follow-up: top five ZIPs (same thread) | 20s | genie | single turn | — | borrower_360 |
| 2 Head of Growth: addressable market | 149s | genie | 5 | omitted (unverifiable figure) | borrower_360, segment_performance_metric_view |
| 3 VP Lending: top 15 candidates | 138s | genie | 6 | yes | borrower_360 |
| 4 Marketing: offer mix deep dive | 104s | genie | 6 | yes | borrower_360 |
| 5 Lock-in cohort | 140s | genie | 7 | yes | lockin_cohort, borrower_360 |
| 6 Retention (original "risk" wording) | 86s | genie | single turn (sweep aborted at the plan screen) | — | borrower_360 |
| Optional: listed-for-sale | 124s | genie | 8 | omitted | borrower_360 |
| Optional: 30-day funnel | 226s | genie | 6 | yes | funnel_snapshot_daily |
| Geography (original ZIP-grain wording) | 201s | genie | 5 (budget cut) | withheld | borrower_360 |
| Investor (original wording) | 192s | genie | 3 | withheld | borrower_opportunity_metric_view, borrower_360 |
| 8 Protected-class refusal | 1.0s | refused | — | — | — |
| 8 Outreach copy refusal | 0.9s | refused | — | — | — |

What the hero answer said on 2026-09-08, as an example of the depth: a
marketable population of about 9.2 million borrowers; the equity segment as
the largest pool; a single standout borrower at opportunity score 91 with a
330 bps spread and 93% equity, ranked at the 99.99th percentile of the
population; and 24 of the top 25 recommended for Refinance + HELOC with an
average spread of 333 bps and 89% equity behind that call.

The retention, geography and investor beats above were re-worded after this
probe (see their beats) so the planner keeps six to nine of nine lines
instead of one. Final verification of the re-worded questions on the
observability build (commit 7ed74cac), same method:

| Beat | Elapsed | Sub-analyses | Synthesis | Sweep log |
|---|---|---|---|---|
| 1 Hero (re-run) | 137s | 6 | yes | planned 7, shipped 7 |
| 6 Retention (re-worded) | 116s | 7 | omitted | planned 7, shipped 7 |
| 7 Geography by state (re-worded) | 154s | 5 | yes | planned 6 (3 screened), 5 shipped, 1 policy-blocked |
| Optional: Investor (re-worded) | 122s | 6 | omitted | planned 7 (3 screened), 6 shipped, 1 policy-blocked |

Every deep beat in this track now ships a multi-section sweep; four of the
eight core beats also ship the verified synthesis on a typical run, and the
others disclose why it was withheld.

Final verification after the business-readable composition landed (per-section
charts, summary first, governed-label literals, guard-aware rewrites; commit
176b2e29, app deployment 8 on 2026-09-08 22:06Z):

| Question | Elapsed | Sections (chart kinds) | Summary |
|---|---|---|---|
| 1 Hero: full analysis of the marketable population | 177s | 7 (state bar, offer bar, cohort bar, offer-code bar, 2 tables, borrower list) | yes |
| 1 Follow-up: top five ZIPs (same thread) | 21s | single turn, ZIP bar | — |
| 2 Head of Growth: addressable market | 119s | 5 (metric, segment bar, offer bar, 2 tables) | yes |
| Cohort comparison (the question that once drew twelve "true" bars) | 96s | 6 (2 metrics, 3 cohort bars, 1 table) | yes |

Section titles on that run were Genie's own: "Marketable population size",
"Offer mix overview", "Why these borrowers", "Offer rationale by cohort",
"Geography concentration", "Signal co-occurrence", "Evidence behind leaders".
