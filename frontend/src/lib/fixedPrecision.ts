/**
 * Fixed-precision numbers that are NOT display text (2026-09-21 audit,
 * responsive-04).
 *
 * lib/formatters owns every number a person reads (en-US, grouped, cached
 * Intl). This module owns the two machine-facing uses of `toFixed`, which
 * must stay ungrouped and locale-free:
 *   - `fixedAttr`: a number inside an attribute string: SVG geometry
 *     (`d`, `points`, `viewBox`) or a `data-*` test hook. "1,234.50" would
 *     be a broken path.
 *   - `roundTo`: rounding a value as DATA (a parsed input, an axis step).
 * ESLint bans `toFixed(` everywhere else in frontend/src (see
 * frontend/eslint.config.js), so a display number cannot sneak in here
 * without a reviewer asking why it is not in lib/formatters.
 */

/** `value` at exactly `digits` decimals, ungrouped: `fixedAttr(1234.5678, 2)` → "1234.57". */
export function fixedAttr(value: number, digits = 2): string {
  return value.toFixed(digits);
}

/** `value` rounded to `digits` decimals, as a number: `roundTo(0.1 + 0.2, 2)` → 0.3. */
export function roundTo(value: number, digits: number): number {
  return Number(value.toFixed(digits));
}
