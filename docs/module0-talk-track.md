# Module 0 Executive Walkthrough

This is the operator script for a public or partner-facing Module 0 demo. It is
intentionally written as a live-run guide, not as a frozen evidence record. Do
not read fixed counts, borrower IDs, or URLs from this document during a demo.
Capture those values from the deployed app and API immediately before recording.

## Preflight

1. Confirm the active Databricks App URL:

   ```bash
   databricks apps get mip-app --profile DEFAULT -o json
   ```

2. Run the rehearsal checklist in
   [module0-rehearsal-checklist.md](module0-rehearsal-checklist.md). Do not
   start the recording until `/api/v1/health`, `/api/v1/data-estate`, `/api/v1/leads`,
   and the first selected Borrower 360 route return live data or an explicit
   degraded state.
3. Confirm public masking is on before any public recording:

   ```bash
   curl "$APP_URL/api/v1/data-estate" | jq '.public_demo_masking'
   ```

4. Use the current top-ranked borrower from the live lead queue, not a fixture:

   ```bash
   curl "$APP_URL/api/v1/leads?limit=1" | jq '.[0].borrower_id'
   ```

5. If a count is material to the narration, open the source chip/proof drawer
   or API response during the run and quote that current value. Do not reuse
   historical counts from older dry runs.

## Story

Opening line:

> "One question, one module: who should we contact, why now, and with what
> offer?"

Positioning:

> "This is the Mortgage Intelligence Platform running as a Databricks App.
> Module 0 focuses on top-of-funnel lead generation and borrower segmentation.
> The app combines a Summit Mortgage synthetic first-party demo feed, Cotality
> public records and mortgage signals, Unity Catalog governed tables and
> functions, Genie, Lakebase state, and Entrada's mortgage-specific scoring and
> workflow layer. In a customer deployment, the same first-party contracts are
> populated by the lender's real LOS, servicing, CRM, interaction, and product
> feeds."

Trust frame:

> "Every number has a source chip or proof drawer. If a source is unavailable,
> the UI says so. MLS listings are now a live Cotality signal, HELOC intent is
> backed by Cotality propensity scoring, and filed building-permit records remain
> visible as a pending data dependency instead of being inferred or fabricated."

> "The important thing for a reviewer is that this is not a dashboard mockup.
> The UI is only the presentation layer. Underneath it are Delta Share inputs,
> silver normalization tables, gold borrower and segment tables, SQL functions,
> semantic views, Genie, and Lakebase audit state. When I click through the app,
> I am not moving through a scripted slideshow. I am moving through the same
> governed assets a lender implementation would use."

## Beat 1: Data Estate

Route: Home.

Show the data-estate panel before the KPI story.

Talk track:

> "This panel is the implementation proof behind the demo. First-party lender
> feeds are represented separately from Cotality enrichment, Databricks governed
> AI assets, and Entrada transformations. Connected assets show row counts and
> freshness. Unconnected assets stay explicit."

> "That distinction matters commercially. Cotality brings the public-records,
> lien, valuation, CLIP, and Owner Link signals. Databricks gives us Unity
> Catalog governance, SQL warehouses, metric views, Genie, Lakebase, deployment,
> and observability. Entrada contributes the mortgage-specific joins, scoring
> contracts, workflow APIs, redaction, and app experience. The product works
> because those layers are separated and inspectable, not because we blended
> everything into an opaque black box."

> "For a lender, the first-party lane is where their own book comes in:
> applications, servicing, CRM, customer interactions, and product balances.
> In this demo workspace those rows are a clearly labeled Summit Mortgage
> synthetic feed so reviewers can see the real ingestion lane and relationship
> scoring path. When a customer connects their actual feeds, the same contracts
> refine suppression, targeting, relationship status, and offer strategy without
> changing the Cotality or Databricks foundation."

Callouts:

- First-party LOS, servicing, CRM, interactions, and product balances are live
  in this demo as `demo_synthetic` Summit rows and become customer-owned feeds
  in production.
- Cotality public records, voluntary lien, AVM, CLIP, and Owner Link power the
  current Module 0 workflow.
- Databricks UC gold tables, Genie, and Lakebase are checked as runtime proof,
  not hard-coded claims.
- Entrada scoring and next-best-offer logic are deterministic SQL contracts,
  backed by tests.

## Beat 2: Portfolio Build

Route: Portfolio Builder.

Use a simple, reproducible filter sequence:

1. Select the configured lender from the target-lender filter.
2. Choose one geography.
3. Choose an equity threshold.
4. Run the build.

Talk track:

> "A growth user can define a population from geography, occupancy, lien
> status, relationship, product fit, and equity. The target-lender filter is
> dictionary-driven, so this product is not hard-coded to a single brand."

> "This is the top-of-funnel moment Cotality described in the working sessions:
> define a lead universe, then overlay triggers and profiles. A user can start
> broad, narrow to a state or market, focus on open liens, look at current or
> former customer relationships, and then ask whether the economics justify
> outreach. The result is a governed population, not a marketing spreadsheet."

> "The target-lender control is intentionally public-safe. The app does not
> expose raw competitor names in public demo mode. It uses governed aliases and
> a lender dictionary, while the raw servicer values remain available only under
> the licensed Unity Catalog boundary. That lets us tell a real competitive
> story without leaking names or pretending the data is ours to publish."

Show that KPI cards update from live query responses. Avoid quoting the trend
line unless the proof drawer/source row shows a current comparison window.

## Beat 3: Segment Intelligence

Route: Segments.

Select "In the Money", then add "Home Equity Candidate", then clear filters.

Talk track:

> "Segment cards are UC-backed definitions, not fuzzy audience labels. Selecting
> multiple segments changes the ranked borrower queue and the map from the same
> filtered population. If the filters do not change the lead queue, that is a
> demo-stopping bug, not expected behavior."

> "The segments are designed to be explainable to a mortgage operator. In the
> Money means the borrower has rate and equity economics. Home Equity Candidate
> means the borrower has strong equity and can be routed toward HELOC or
> cash-out conversations. Investor or Multi-Property comes from Owner Link and
> ownership patterns. Retention Risk is current-customer risk, not a generic
> churn label. Those definitions are testable and can be debated with a credit,
> sales, or compliance team."

> "Listed for Sale is now live from Cotality MLS/Listings, so it behaves like
> the other evidence-backed segments. The remaining source gap is true filed
> Building Permits. We do not infer permit activity from the HELOC propensity
> model; the app labels that separately as HELOC Intent and keeps Building
> Permits in roadmap status until the real feed lands."

Show:

- In-the-money definition from the source chip.
- Listed for Sale as a live MLS-backed segment.
- Building Permits as the explicit remaining data gap.
- PII suppressed badge.

## Beat 4: Genie as Control Layer

Route: Ask Genie.

Recommended prompt:

> "Which ZIPs have the most in-the-money refinance candidates?"

What to show:

1. The chart treats ZIPs as categorical labels.
2. The table shows ZIP, state, borrower count, and score columns with ZIPs as
   strings.
3. "Show proof" exposes SQL/source/freshness when Genie returns trusted
   evidence.
4. Run "Open this cohort in Lead Queue" only after the action preview shows the
   same ZIP/state/segment filters from the answer.

Talk track:

> "Genie is not only answering a question. It can hand a governed result to the
> app as an action, but actions are confirmed and audited. The resulting lead
> queue must preserve the answer filters; otherwise the app is wrong and the
> action should not be used."

> "This is the most important part of the story. Genie is not a side chat box.
> It is becoming a control layer for the data product. A business user can ask a
> free-form question, get an answer from trusted mortgage assets, inspect the
> proof, and then convert that answer into a workflow. The workflow is still
> governed: the app previews the filters it is about to apply, asks for
> confirmation, writes the cohort state, and routes the user into the same lead
> queue the rest of the product uses."

> "The chart planner is also intentionally strict. A ZIP code is a geography
> label, not a number to average or trend. Borrower IDs, FIPS, ZIP, and similar
> identifiers must remain categorical. Measures like borrower count, average
> score, equity, rate spread, and conversion are the only values that belong on
> numeric axes. That rule exists because a pretty chart with the wrong semantic
> type destroys trust immediately."

## Beat 5: Lead Queue to Borrower 360

Route: Lead Queue, then click the top borrower from the live filtered list.

Talk track:

> "The lead queue ranks borrowers from `mip.gold.lead_population`. The borrower
> ID, city, ZIP, segments, score, and next-best-offer are masked or public-safe
> values in the app. Raw CLIP and Owner Link are not shown in public demo mode."

In Borrower 360:

> "This is the per-borrower explanation. It shows the selected household's
> current lien, AVM/equity, segment membership, why-now rationale, and source
> evidence. The visible source references are governed aliases; customer teams
> can join back to raw identifiers inside Unity Catalog when licensed and
> authorized."

> "This is where the demo moves from an aggregate story to a borrower story
> without becoming unsafe. We can show why this household is ranked, why the
> offer was chosen, which source events support the recommendation, and how the
> app suppresses raw identifiers. Mortgage experts should be able to challenge
> the math here. If they ask how the spread was calculated, where the equity
> came from, or why a segment fired, the page should give the answer or open the
> source drawer that does."

## Beat 6: Offer and Approval

Route: Offer Orchestrator for the selected live borrower.

Talk track:

> "Next-best-offer is a deterministic SQL decision tree, not a free-form model
> decision. The draft is review-only until a human approves it. Approval writes
> to Lakebase with actor, action, entity, evidence, timestamp, and request ID."

> "The reason this matters is governance. The app can help a sales or marketing
> team move faster, but it should not silently send regulated outreach. It
> recommends, explains, drafts, and queues. A human approves. The audit row
> records what happened. If Lakebase is down, approval fails visibly and no
> success state is shown. That is the behavior reviewers should expect from an
> enterprise Databricks App."

Only click approval in a rehearsal/demo workspace where writing an audit row is
expected.

## Close

> "Module 0 answers the practical growth question: who should we contact, why
> now, and with what offer. The differentiator is that the answer is governed:
> source data, scoring logic, Genie answer, cohort action, borrower evidence,
> outreach draft, and approval audit all sit on the same Databricks-backed
> implementation path."

> "The immediate path forward is clear. MLS listings already unlock a purchase
> trigger, Cotality propensity scores enrich HELOC and refinance intent, and
> filed building permits remain the next high-intent overlay once that feed is
> approved. First-party lender feeds refine relationship and suppression. The
> same architecture then extends into pipeline pull-through, loan officer
> workflow, underwriting support, and portfolio risk. But the foundation starts
> here: build the right lead population from governed data, explain the ranking,
> and turn a trusted answer into an auditable action."

## Known Data Dependencies

- Cotality MLS/Listings is connected through `mip.silver.listing_activity` and
  powers the live Listed for Sale segment.
- Cotality HELOC and Refi propensity scores are connected through curated silver
  tables and power HELOC/refi intent evidence.
- Cotality Building Permits Delta Share is still required before filed-permit
  remodel triggers can become live. Do not describe propensity as a filed permit.
- The Summit Mortgage first-party lane is synthetic demo data by design. Set
  `MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=0` before connecting real customer feeds.

## Do Not Say

- Do not say every asset is live if `/api/v1/data-estate` shows unconnected or
  pending assets.
- Do not quote borrower names, raw CLIP, raw Owner Link, or unmasked addresses
  in a public recording.
- Do not claim Genie actions work unless the filtered destination route is shown
  and matches the source answer.
- Do not call MLS or permit overlays implemented until those Cotality shares are
  connected and the corresponding segment counts come from live tables.
