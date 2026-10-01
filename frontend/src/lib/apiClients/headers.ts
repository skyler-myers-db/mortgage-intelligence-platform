/**
 * Response-header validators the typed clients share (audit delivery-06,
 * D-platform-process-e1 items 7-9, client half).
 *
 * `X-Data-Last-Good-At` (backend/services/server_timing.py): set only when
 * the served value was retained after a failed cache refresh, as the OLDEST
 * last successful Unity Catalog read of the request, `YYYY-MM-DDTHH:MM:SSZ`.
 * Anything else (a missing header, another shape, an impossible date) reads
 * as "not stale". The header is never read on a non-2xx response:
 * apiTransport's request path throws before a caller sees the headers, and
 * the server appends it to 2xx responses only (W5a ruling).
 */

const LAST_GOOD_AT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;

/** The validated `X-Data-Last-Good-At` instant, or null. */
export function lastGoodAtHeader(headers: Headers): string | null {
  const value = headers.get('X-Data-Last-Good-At');
  if (!value || !LAST_GOOD_AT_RE.test(value)) return null;
  return Number.isFinite(Date.parse(value)) ? value : null;
}

const DATA_REFRESHED_AT_RE = /^[0-9TZ:.+-]+$/;

/**
 * `X-Data-Refreshed-At` (the gold refresh instant on GET /leads), loosely
 * shaped. A copy of apiClients/leads.ts's validator, which keeps its own
 * until the Lead Queue's client adopts this module.
 */
export function dataRefreshedAtHeader(value: string | null): string | null {
  return value && DATA_REFRESHED_AT_RE.test(value) ? value : null;
}

/** A read with the age of the value it carries: `lastGoodAt` is set only on a retained (stale) serve. */
export interface Fresh<T> {
  data: T;
  lastGoodAt: string | null;
}
