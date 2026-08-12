/**
 * The `(city, state)` cohort filter, frontend side.
 *
 * Mirrors `backend/schemas/genie_geo_filters.py`. One key, `cities`, always
 * plural, whose values are `CITY~ST` pairs.
 *
 * A pair and never a bare name: `mip.gold.borrower_360` holds 428 distinct
 * city names but 433 distinct `(city, state)` pairs, and five names span two
 * states with a tiny minority side — CYPRESS is CA 14,630 / TX 1, so a
 * name-only filter is wrong by 14,631x on the TX side. `~` is RFC-3986
 * unreserved, so `URLSearchParams` leaves it literal and a shared link reads
 * `?cities=CHICAGO~IL`.
 */

export const CITY_STATE_SEPARATOR = '~';

/** `CITY~ST`. Mirrors `CITY_STATE_PAIR_RE`; keep the two in step. */
export const CITY_STATE_PAIR_RE = /^[A-Z][A-Z .-]{0,47}~[A-Z]{2}$/;

/** 2-letter USPS state code, as emitted by the gold tables. */
export const CITY_STATE_CODE_RE = /^[A-Z]{2}$/;

/** Gold stores city uppercase; collapse whitespace so hand-typed input matches. */
export function normalizeCityName(value: string): string {
  return value.trim().toUpperCase().replace(/\s+/g, ' ');
}

/**
 * Build one `CITY~ST` token, or `null` when either half is unusable.
 *
 * Returning `null` must mean "render plain text" at every call site. Falling
 * back to the state is the 2.3x silent substitution this filter replaces.
 */
export function formatCityStatePair(city: unknown, state: unknown): string | null {
  if (typeof city !== 'string' || typeof state !== 'string') return null;
  const cityText = normalizeCityName(city);
  const stateText = state.trim().toUpperCase();
  if (!cityText || !CITY_STATE_CODE_RE.test(stateText)) return null;
  const token = `${cityText}${CITY_STATE_SEPARATOR}${stateText}`;
  return CITY_STATE_PAIR_RE.test(token) ? token : null;
}

/** Columns a Genie answer uses for the city half of the key. */
export const CITY_COLUMNS = new Set(['city', 'situs_city']);

/** Columns a Genie answer uses for the state half of the key. */
export const CITY_STATE_COLUMNS = new Set(['state', 'state_code']);

/**
 * Read the state that sits beside a city IN THE SAME ROW.
 *
 * The row is the unit. A state taken from anywhere else — the answer's overall
 * state list, a neighbouring row — pairs a city with a state the answer never
 * put it in, which is how `CYPRESS~TX` gets invented from a CA row.
 */
export function rowStateFor(row: Record<string, unknown> | null | undefined): string | null {
  if (!row) return null;
  for (const [key, value] of Object.entries(row)) {
    if (!CITY_STATE_COLUMNS.has(key.trim().toLowerCase())) continue;
    if (typeof value !== 'string') continue;
    const state = value.trim().toUpperCase();
    if (CITY_STATE_CODE_RE.test(state)) return state;
  }
  return null;
}
