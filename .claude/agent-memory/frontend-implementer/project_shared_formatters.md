---
name: project-shared-formatters
description: frontend/src/lib/formatters.ts is the single home for unit-bearing display formatting (bps, compact currency, rate percents) — import it instead of hand-rolling in JSX.
metadata:
  type: project
---

`frontend/src/lib/formatters.ts` owns every unit the product renders:
`currency`, `compactCurrency` (K→M rollup), `signedBps` / `signedBpsLabel`,
`ratePct` (input already 0-100) and `ratePctFromFraction` (input 0-1).

**Why:** the 2026-08-07 rendering audit found the same class of bug at six
independent call sites because each one formatted its own unit inline —
`+{rate_spread_bps}` on a signed field produced `+-422 bps`,
`$${v/1000}k` produced `$4410k`, and a raw `{current_rate}%` next to a
`(market_rate*100).toFixed(3)` put two rate precisions on one screen. The
formatter module (PR #166) plus `formatters.test.ts` pins each of those cases.

**How to apply:** when rendering a number with a unit, import from
`lib/formatters` — do not write a template literal with `$`, `%`, `bps`, or a
`/1000` in JSX. If a new unit is needed, add it there with a doc comment naming
the gold column's unit (SQL `COMMENT ON COLUMN` is authoritative: e.g.
`current_rate` is percent form, `market_rate_fraction` is a fraction,
`rate_spread_bps` is signed, `equity_pct`/`ltv` are integer percent 0-100).
The Genie answer layer has its own column-semantics layer on top
(`GenieAnswer.logic.ts`: `isIdentifierColumn` / `isMoneyColumn` /
`coerceMeasure`) — a numeric ZIP must never be localized or used as a measure.
