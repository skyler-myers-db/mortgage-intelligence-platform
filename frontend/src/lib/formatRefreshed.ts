/**
 * Format an ISO timestamp in the viewer's local timezone. Used by the
 * "Refreshed …" chip on Home + Portfolio Builder. Returns `null` when the
 * input is `null`/`undefined`/unparseable so the caller can hide the chip
 * rather than render an em-dash.
 *
 * Uses `Intl.DateTimeFormat` with the runtime's resolved timezone. Same
 * format on every page: `Refreshed Apr 23, 4:07 PM PDT`.
 */
export function formatRefreshed(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  try {
    const fmt = new Intl.DateTimeFormat(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZoneName: 'short',
    });
    return `Refreshed ${fmt.format(d)}`;
  } catch {
    // Absent-locale fallback.
    return `Refreshed ${d.toISOString()}`;
  }
}
