import { PageShell } from '../components/layout/PageShell';
import { AdminAuditExplorer } from '../components/admin/AdminAuditExplorer';

/**
 * Audit ledger — the append-only Lakebase ledger on its own page
 * (D-audit-reads-c3; audit flow-04, tables-10, critic-03).
 *
 * "Audit" is the product promise's last verb, and the explorer used to sit
 * at the bottom of a 3,500px Administration page that only administrators
 * could open. It now has its own route, gated by app.tsx's AuditLedgerGate to
 * administrators and the read-only Auditor role (the same decision
 * `require_audit_reader` enforces), and reached from the rail, the Admin page's
 * ledger card, the command palette and, for a non-admin auditor, the route
 * nav. A declared departure from the single-screen prototype
 * (design_files/Module 0 Prototype.html:1188-1204;
 * deviation:audit-ledger-route); the explorer itself is unchanged prototype
 * BEM (`.surface`, `.tbl`, `.chip`) and keeps `id="audit"`, so every
 * `?audit_event_id=…#audit` link opens it on its row.
 *
 * Every ledger read the explorer makes (a page, a filter change, a page turn,
 * the rollups) is itself recorded server-side as one VIEW_AUDIT_LEDGER row,
 * so the explorer reads only on an explicit open or interaction: never on
 * hover, prefetch, a poll or window focus.
 */
export default function AuditLedger() {
  return (
    <PageShell
      eyebrow="Compliance"
      title="Audit ledger"
      lede="Every governed decision, export and borrower-level read, append-only in Lakebase. Reading this ledger is itself recorded."
    >
      <AdminAuditExplorer />
      {/* W5c (w5-refusal-capture-sales) mounts RefusalReportsPanel here, behind the same gate. */}
    </PageShell>
  );
}
