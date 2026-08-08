/**
 * Shared display formatters for the unit-bearing numbers Module 0 renders.
 *
 * Every one of these units was, at some point, hand-rolled per call site —
 * which is exactly how the 2026-08-07 rendering audit found `+-422 bps`
 * (a hardcoded "+" in front of a signed value), `$4410k` (a thousands
 * formatter with no millions branch), and two different rate precisions on
 * one screen. Formatting a unit is a product decision, not a template
 * detail: put it here and import it, don't re-derive it in JSX.
 */

export const currency = (value: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
export const pct = (value: number) => `${value.toFixed(1)}%`;

/** Rendered when a value is missing or non-finite — never a fabricated 0. */
const UNKNOWN = '—';

/**
 * Signed basis-point value, WITHOUT the unit suffix (for a column already
 * headed "Rate Δ (bps)").
 *
 * `rate_spread_bps` is signed by construction (`fn_rate_spread` =
 * `BROUND((current_rate - market_rate) * 10000)`, unclamped): positive is
 * above market (a refi opportunity), negative is below market, zero is at
 * market. So the sign belongs to the VALUE, never to the template:
 *   180  → "+180"   (above market)
 *   -422 → "-422"   (below market — the "+-422" defect)
 *   0    → "0"      (at market: neither "+0" nor "-0" is true)
 */
export function signedBps(value: number): string {
  if (!Number.isFinite(value)) return UNKNOWN;
  // Math.round(-0.4) is -0; comparing to 0 catches it so we never emit "-0".
  const rounded = Math.round(value);
  if (rounded === 0) return '0';
  return rounded > 0 ? `+${rounded}` : `${rounded}`;
}

/** `signedBps` with the unit: "+180 bps" / "-422 bps" / "0 bps". */
export function signedBpsLabel(value: number): string {
  const magnitude = signedBps(value);
  return magnitude === UNKNOWN ? UNKNOWN : `${magnitude} bps`;
}

/**
 * Compact USD for dense surfaces (table cells, preview grids, narrative
 * prose): rolls up through K *and* M so a $4.41M equity position reads as
 * "$4.4M" instead of "$4410K".
 *
 *   4_410_000 → "$4.4M"      806_500 → "$807K"      950 → "$950"
 *
 * The K→M boundary is applied AFTER rounding so 999_600 renders "$1.0M",
 * not "$1000K". Use `currency()` instead where the exact dollar figure is
 * the point (dossier facts, governed thresholds).
 */
export function compactCurrency(value: number): string {
  if (!Number.isFinite(value)) return UNKNOWN;
  const sign = value < 0 ? '-' : '';
  const abs = Math.abs(value);
  if (abs >= 1_000) {
    const thousands = Math.round(abs / 1_000);
    if (thousands >= 1_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
    return `${sign}$${thousands}K`;
  }
  return `${sign}$${Math.round(abs)}`;
}

/**
 * A rate already in PERCENT form (0-100), at the product's one rate
 * precision. `mip.gold.borrower_360.current_rate` is `first_pos_rate * 100`
 * with no ROUND in SQL, so `0.07 * 100` reaches the UI as
 * 7.000000000000001 — rendering it raw is an IEEE754 leak.
 */
export function ratePct(percentValue: number): string {
  if (!Number.isFinite(percentValue)) return UNKNOWN;
  return `${percentValue.toFixed(2)}%`;
}

/**
 * A rate in FRACTION form (0-1) — e.g. `market_rate_fraction`, which the
 * borrower router maps to `WhyPanel.market_rate`. Scales once, then renders
 * at the same precision as `ratePct` so a borrower's own rate and the par
 * rate beside it can't disagree about how many decimals a rate has.
 */
export function ratePctFromFraction(fraction: number): string {
  if (!Number.isFinite(fraction)) return UNKNOWN;
  return ratePct(fraction * 100);
}
