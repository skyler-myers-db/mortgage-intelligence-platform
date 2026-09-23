/**
 * The product's two CSV primitives, shared by the Lead Queue export
 * (components/mortgage/LeadTable.csv.ts) and the audit explorer's page export
 * (components/admin/AdminAuditExplorer.csv.ts), so both write cells through
 * the same formula-injection gate.
 */

/**
 * Formula-injection-safe CSV cell: a leading `= + - @` is neutralised with a
 * quote prefix before the usual quoting.
 */
export function csvEscape(raw: string): string {
  const v = /^[=+\-@]/.test(raw) ? `'${raw}` : raw;
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

/** Anchor-download CSV text the browser already holds. No server round trip. */
export function downloadCsvText(csv: string, filename: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
