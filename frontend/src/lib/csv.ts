/**
 * The product's two CSV primitives, shared by the Lead Queue export
 * (components/mortgage/LeadTable.csv.ts), the audit explorer's page export
 * (components/admin/AdminAuditExplorer.csv.ts) and the Genie answer export
 * (components/mortgage/GenieAnswer.export.ts), so all three write cells
 * through the same formula-injection gate.
 */

/**
 * Formula-injection-safe CSV cell: a leading `= + - @`, tab or carriage
 * return is neutralised with a quote prefix before the usual quoting (a
 * spreadsheet can drop a leading TAB or CR and read what follows as a
 * formula; OWASP CSV injection). A cell holding a quote, comma, LF or CR is
 * quoted, so no line break inside a value can start a new row.
 */
export function csvEscape(raw: string): string {
  const v = /^[=+\-@\t\r]/.test(raw) ? `'${raw}` : raw;
  return /[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
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
