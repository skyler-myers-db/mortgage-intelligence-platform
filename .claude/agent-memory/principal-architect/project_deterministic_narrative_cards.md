---
name: deterministic-narrative-cards
description: Repo pattern — "AI summary" narrative cards are built deterministically + numeric-verified, not via live Genie, for booth reliability.
metadata:
  type: project
---

The repo has an established, approved pattern for plain-English "AI summary" UI: compose the narrative DETERMINISTICALLY from data already loaded on the page, route every number through a numeric-claims verifier (token must equal its grounded source value; a stray-number scan flags any prose number not backed by a claim), and label it honestly as "traces to the gold snapshot" — NOT as a live AI generation. Reference implementations: `frontend/src/lib/borrowerStory.ts` + `BorrowerStoryCard.tsx` (borrower "Tell the story"), and `frontend/src/lib/portfolioStory.ts` + `PortfolioSummaryCard.tsx` (Home "Your book today", branch feat/portfolio-summary).

**Why:** A literal live-Genie call on the hottest routes adds latency + a failure surface at the DAIS booth. Deterministic composition is instant and never fails. The numeric verifier is what makes the honest "grounded" badge defensible.

**How to apply:** When asked for an "AI/Genie-generated summary" in the UI, the deterministic-but-honestly-labelled approach satisfies intent AS LONG AS the labeling never claims live AI generation. If a customer (e.g. Cotality) was specifically promised a *live Genie* call as a differentiator, flag that the deterministic card is a non-blocking follow-up swap, not a substitute for that specific commitment — the honest label closes the truthfulness gap but not necessarily a literal-feature gap. A live-Genie variant is a clean follow-up swap behind the same card.
