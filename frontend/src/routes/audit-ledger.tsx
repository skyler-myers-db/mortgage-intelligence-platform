import { lazy, Suspense, useState } from 'react';
import { PageShell } from '../components/layout/PageShell';
import { AdminAuditExplorer } from '../components/admin/AdminAuditExplorer';

// D-audit-reads-d: its own chunk, fetched on the explicit open only.
const RefusalReportsPanel = lazy(() => import('../components/admin/RefusalReportsPanel'));

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
 * hover, prefetch, a poll or window focus. The refusal reports below follow
 * the same rule more strictly: nothing loads with the route; "Show refusal
 * reports" loads the panel chunk and makes its first (recorded) list read
 * (deviation:refusal-reports-panel).
 */
function RefusalReportsSlot() {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-grid">
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        aria-expanded={open}
        aria-controls={open ? 'refusal-reports' : undefined}
        onClick={() => setOpen((current) => !current)}
        data-testid="refusal-reports-toggle"
      >
        {open ? 'Hide refusal reports' : 'Show refusal reports'}
      </button>
      {open && (
        <div id="refusal-reports">
          <Suspense
            fallback={
              <div className="surface mt-grid" role="status" aria-busy="true">
                <div className="surface__hdr">
                  <span className="skeleton skeleton--heading" aria-hidden="true" />
                </div>
                <div className="surface__body surface__body--reserve">
                  <span className="sr-only">Loading refusal reports</span>
                </div>
              </div>
            }
          >
            <RefusalReportsPanel />
          </Suspense>
        </div>
      )}
    </div>
  );
}

export default function AuditLedger() {
  return (
    <PageShell
      eyebrow="Compliance"
      title="Audit ledger"
      lede="Every governed decision, export and borrower-level read, append-only in Lakebase. Reading this ledger is itself recorded."
    >
      <AdminAuditExplorer />
      <RefusalReportsSlot />
    </PageShell>
  );
}
