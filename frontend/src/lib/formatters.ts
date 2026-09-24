/**
 * Shared display formatters for the unit-bearing numbers Module 0 renders.
 *
 * Every one of these units was, at some point, hand-rolled per call site —
 * which is exactly how the 2026-08-07 rendering audit found `+-422 bps`
 * (a hardcoded "+" in front of a signed value), `$4410k` (a thousands
 * formatter with no millions branch), and two different rate precisions on
 * one screen; and how the 2026-09-21 audit (responsive-04) found the same
 * addressable count as "89,553" on Home and "89.55K" on Analytics, five
 * compact-USD formatters that disagreed (`$-4.41M`, `$1000K`), and 87 bare
 * `toLocaleString()` calls that followed the BROWSER locale while currency
 * was pinned to en-US, so separators could disagree on one screen.
 *
 * Formatting a unit is a product decision, not a template detail: put it
 * here and import it, don't re-derive it in JSX. ESLint enforces it
 * (`no-restricted-syntax` in frontend/eslint.config.js bans bare
 * `toLocaleString()`, `toFixed(`, `toLocaleDateString(` and `new Intl.*`
 * outside this module and lib/time.ts).
 *
 * Every formatter is an en-US `Intl.NumberFormat` built ONCE at module load
 * (constructing one per table cell is measurable on a 120-row virtualized
 * queue). Missing or non-finite input renders the unknown glyph, never a
 * fabricated 0 and never "NaN".
 */

/** The product's one number locale: a US mortgage product, USD amounts. */
const LOCALE = 'en-US';

/** Rendered when a value is missing or non-finite — never a fabricated 0. */
export const UNKNOWN_VALUE = '—';
const UNKNOWN = UNKNOWN_VALUE;

type MaybeNumber = number | null | undefined;

function isKnown(value: MaybeNumber): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * Collapse a value that rounds to zero at `fractionDigits` onto +0, so no
 * formatter ever renders "-0", "-0.0" or "-$0" (Intl's default signDisplay
 * prints negative zero; `signDisplay: 'negative'` is ES2023 and outside this
 * project's ES2020 lib).
 */
function unsignedZero(value: number, fractionDigits: number): number {
  return Math.abs(value) < 0.5 / 10 ** fractionDigits ? 0 : value;
}

const COUNT = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
// Byte-for-byte what a bare `n.toLocaleString()` printed under en-US.
const NUMBER = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 3 });
const COMPACT = new Intl.NumberFormat(LOCALE, { notation: 'compact', maximumFractionDigits: 1 });
const USD = new Intl.NumberFormat(LOCALE, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const USD_ONE_DECIMAL = new Intl.NumberFormat(LOCALE, {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const BPS = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0, signDisplay: 'exceptZero' });

const FIXED = new Map<number, Intl.NumberFormat>();
const SIGNED_FIXED = new Map<number, Intl.NumberFormat>();
const PERCENT = new Map<number, Intl.NumberFormat>();

function fixedFormatter(digits: number): Intl.NumberFormat {
  let formatter = FIXED.get(digits);
  if (!formatter) {
    formatter = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });
    FIXED.set(digits, formatter);
  }
  return formatter;
}

function signedFixedFormatter(digits: number): Intl.NumberFormat {
  let formatter = SIGNED_FIXED.get(digits);
  if (!formatter) {
    formatter = new Intl.NumberFormat(LOCALE, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
      signDisplay: 'exceptZero',
    });
    SIGNED_FIXED.set(digits, formatter);
  }
  return formatter;
}

function percentFormatter(digits: number): Intl.NumberFormat {
  let formatter = PERCENT.get(digits);
  if (!formatter) {
    formatter = new Intl.NumberFormat(LOCALE, {
      style: 'percent',
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    PERCENT.set(digits, formatter);
  }
  return formatter;
}

/** A whole count with grouping: 89553 → "89,553". Rounds half up, like the KPI cards always did. */
export function formatCount(value: MaybeNumber): string {
  if (!isKnown(value)) return UNKNOWN;
  // `+ 0` turns Math.round's -0 (e.g. for -0.4) into 0.
  return COUNT.format(Math.round(value) + 0);
}

/**
 * A number that may carry a fraction (a chart value, a Genie cell): up to
 * three fraction digits, grouped. Use `formatCount` for counts.
 */
export function formatNumber(value: MaybeNumber): string {
  if (!isKnown(value)) return UNKNOWN;
  return NUMBER.format(unsignedZero(value, 3));
}

/** Exactly `digits` fraction digits, grouped: the display replacement for `toFixed`. */
export function formatFixed(value: MaybeNumber, digits: number): string {
  if (!isKnown(value)) return UNKNOWN;
  return fixedFormatter(digits).format(unsignedZero(value, digits));
}

/**
 * Compact count for dense chart marks (axis ticks, bar values, sublabels):
 * one fraction digit, so ticks read "24.1K / 18.1K", never "18.08K".
 * Headline KPIs use `formatCount` so the same metric reads the same on
 * every route.
 */
export function formatCompact(value: MaybeNumber): string {
  if (!isKnown(value)) return UNKNOWN;
  return COMPACT.format(unsignedZero(value, 1));
}

/** Whole US dollars with grouping: 806500 → "$806,500"; the sign precedes the symbol. */
export function formatUsd(value: MaybeNumber): string {
  if (!isKnown(value)) return UNKNOWN;
  return USD.format(unsignedZero(value, 0));
}

/** Alias kept for existing call sites; see `formatUsd`. */
export const currency = (value: number): string => formatUsd(value);

const LARGE_USD_UNITS = [
  [1_000_000, 'M'],
  [1_000_000_000, 'B'],
] as const;

/**
 * Compact USD for dense surfaces (table cells, preview grids, narrative
 * prose, the ROI projector): whole thousands, then one decimal from millions
 * up, so a $4.41M equity position reads "$4.4M" instead of "$4410K".
 *
 *   4_410_000 → "$4.4M"   806_500 → "$807K"   950 → "$950"   -4_410_000 → "-$4.4M"
 *
 * The unit is chosen AFTER rounding, at every boundary: 999_600 renders
 * "$1.0M", never "$1000K", and $999.6 renders "$1K". The sign sits before
 * the "$" ("-$4.4M", never "$-4.41M"), and a value that rounds to zero is
 * "$0". Use `formatUsd` where the exact dollar figure is the point (dossier
 * facts, governed thresholds).
 */
export function formatUsdCompact(value: MaybeNumber): string {
  if (!isKnown(value)) return UNKNOWN;
  const abs = Math.abs(value);
  const sign = value < 0 ? -1 : 1;
  const dollars = Math.round(abs);
  if (dollars < 1_000) return USD.format(dollars === 0 ? 0 : sign * dollars);
  const thousands = Math.round(abs / 1_000);
  if (thousands < 1_000) return `${USD.format(sign * thousands)}K`;
  for (const [divisor, suffix] of LARGE_USD_UNITS) {
    // toFixed rounds the exact binary value half-up, which is what the
    // one-decimal Intl formatter below does for a positive magnitude, so
    // the unit test and the rendered digits agree at every boundary.
    const scaled = Number((abs / divisor).toFixed(1));
    if (scaled < 1_000) return `${USD_ONE_DECIMAL.format(sign * scaled)}${suffix}`;
  }
  return `${USD_ONE_DECIMAL.format(sign * Number((abs / 1_000_000_000_000).toFixed(1)))}T`;
}

/** Alias kept for existing call sites; see `formatUsdCompact`. */
export const compactCurrency = (value: number): string => formatUsdCompact(value);

/** A ratio in FRACTION form (0-1) as a percent: 0.0427 → "4.3%". */
export function formatPercent(fraction: MaybeNumber, digits = 1): string {
  if (!isKnown(fraction)) return UNKNOWN;
  return percentFormatter(digits).format(unsignedZero(fraction, digits + 2));
}

/** A value already in PERCENT form (0-100): 42.37 → "42.4%". */
export function pct(percentValue: MaybeNumber, digits = 1): string {
  if (!isKnown(percentValue)) return UNKNOWN;
  return `${formatFixed(percentValue, digits)}%`;
}

/**
 * A signed change in PERCENT form, for KPI deltas: 3.24 → "+3.2%",
 * -1.5 → "-1.5%", and anything that rounds to zero → "0.0%" (never "-0.0%",
 * which a hand-rolled `${sign}${toFixed(1)}` printed for -0.04).
 */
export function signedPct(percentValue: MaybeNumber, digits = 1): string {
  if (!isKnown(percentValue)) return UNKNOWN;
  return `${signedFixedFormatter(digits).format(unsignedZero(percentValue, digits))}%`;
}

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
 * Whole basis points, grouped (1250 → "+1,250"), via Intl signDisplay.
 */
export function signedBps(value: number): string {
  if (!isKnown(value)) return UNKNOWN;
  return BPS.format(unsignedZero(value, 0));
}

/** `signedBps` with the unit: "+180 bps" / "-422 bps" / "0 bps". */
export function signedBpsLabel(value: number): string {
  const magnitude = signedBps(value);
  return magnitude === UNKNOWN ? UNKNOWN : `${magnitude} bps`;
}

/**
 * Unsigned whole basis points with the unit, for prose that already states
 * the direction ("180 bps above market").
 */
export function bpsLabel(value: MaybeNumber): string {
  const magnitude = formatCount(value);
  return magnitude === UNKNOWN ? UNKNOWN : `${magnitude} bps`;
}

/**
 * A low-high estimate range, collapsed to a single value when the two ends
 * render identically. A confidence band whose ends are equal is a POINT
 * estimate, and "$100,000-$100,000" reads as a formatting bug rather than as
 * the confident number it is — the shape every row takes when the band
 * columns are absent and both ends fall back to the point value.
 *
 * The comparison is on the FORMATTED ends, so two inputs that round to the
 * same displayed value (100_000.2 / 100_000.4 under `currency`) also collapse
 * instead of printing a range between two identical strings.
 */
export function rangeLabel(
  low: number,
  high: number,
  format: (value: number) => string,
): string {
  const lowLabel = format(low);
  const highLabel = format(high);
  return lowLabel === highLabel ? lowLabel : `${lowLabel}-${highLabel}`;
}

/**
 * Display LTV, an integer percent (0-500; underwater borrowers exceed 100).
 *
 * `null` is the API's explicit "withheld": `Borrower360.ltv` is `int | None`
 * on the wire and is None whenever gold flags `ltv_basis_is_unreliable`.
 * Interpolating it raw rendered "null%" (2026-09-21 audit, quality-04). It
 * renders the unknown glyph instead — never a fabricated 0%, which on a
 * contact-prioritization surface reads as "free and clear".
 */
export function ltvPct(value: number | null | undefined): string {
  if (!isKnown(value)) return UNKNOWN;
  return `${Math.round(value)}%`;
}

/**
 * A rate already in PERCENT form (0-100), at the product's one rate
 * precision. `mip.gold.borrower_360.current_rate` is `first_pos_rate * 100`
 * with no ROUND in SQL, so `0.07 * 100` reaches the UI as
 * 7.000000000000001 — rendering it raw is an IEEE754 leak.
 */
export function ratePct(percentValue: number): string {
  if (!isKnown(percentValue)) return UNKNOWN;
  return `${formatFixed(percentValue, 2)}%`;
}

/**
 * A rate in FRACTION form (0-1) — e.g. `market_rate_fraction`, which the
 * borrower router maps to `WhyPanel.market_rate`. Scales once, then renders
 * at the same precision as `ratePct` so a borrower's own rate and the par
 * rate beside it can't disagree about how many decimals a rate has.
 */
export function ratePctFromFraction(fraction: number): string {
  if (!isKnown(fraction)) return UNKNOWN;
  return ratePct(fraction * 100);
}
