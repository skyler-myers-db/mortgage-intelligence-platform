/**
 * Format an ISO timestamp for the "Refreshed …" chip on Home + Portfolio
 * Builder.
 *
 * Returns `null` when the input is `null`/`undefined`/unparseable so the
 * caller can hide the chip rather than render an em-dash. Otherwise
 * returns `{display, iso}`:
 *   - `display` is rendered in the viewer's local timezone for "feels
 *     recent" UX (`Refreshed Apr 23, 4:07 PM PDT`).
 *   - `iso` is the unambiguous ISO-8601 UTC form suitable for a native
 *     `title={...}` tooltip on the same element. Two operators in
 *     different timezones comparing a Slack screenshot can hover to
 *     disambiguate the display string. Fixes R5-19 (2026-04-23).
 *
 * Callers should pass the `iso` string to `title=` on the chip element
 * (Chip supports it; Topbar pills already render a native `title`).
 */
export interface RefreshedLabel {
  /** Viewer-local, human-readable. e.g. `Refreshed Apr 23, 4:07 PM PDT`. */
  display: string;
  /** Canonical ISO-8601 UTC. Use as `title={iso}` to disambiguate timezone. */
  iso: string;
}

export function formatRefreshed(iso: string | null | undefined): RefreshedLabel | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const canonical = d.toISOString();
  try {
    const fmt = new Intl.DateTimeFormat(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZoneName: 'short',
    });
    return { display: `Refreshed ${fmt.format(d)}`, iso: canonical };
  } catch {
    // Absent-locale fallback.
    return { display: `Refreshed ${canonical}`, iso: canonical };
  }
}
