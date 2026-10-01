import { useQueryClient } from '@tanstack/react-query';
import { PageShell } from '../components/layout/PageShell';
import { AccessDenied } from '../components/ui/AccessDenied';

/**
 * What an actor who is neither an administrator nor a configured auditor sees
 * on an /audit-ledger deep link (D-audit-reads-c3). The same 403 surface as
 * the admin route (admin-config.access-denied.tsx, audit shell-06): the
 * required role and a way back, never a silent redirect.
 *
 * Loaded lazily from app.tsx's AuditLedgerGate, so the denied page costs the
 * initial bundle nothing. It loads no ledger chunk and issues no ledger read:
 * the gate decides from the server-authoritative session before this mounts.
 */
interface AuditLedgerAccessDeniedRouteProps {
  /** The session check failed rather than answering "no". */
  unverified?: boolean;
}

export default function AuditLedgerAccessDeniedRoute({ unverified = false }: AuditLedgerAccessDeniedRouteProps) {
  const queryClient = useQueryClient();

  return (
    <PageShell
      eyebrow="Compliance"
      title={unverified ? 'Access could not be confirmed' : 'Auditor access required'}
      lede="The audit ledger is limited to administrators and the read-only Auditor role."
    >
      <AccessDenied
        title={unverified ? 'Access check did not complete' : 'This page is limited to administrators and auditors'}
        requiredRole="Administrator or Auditor"
        status={unverified ? 'unverified' : 'denied'}
        onRetry={() => {
          void queryClient.invalidateQueries({ queryKey: ['session', 'access'] });
        }}
        testId="audit-ledger-access-denied"
      >
        The audit ledger lists every governed decision, export and borrower-level read, and
        reading it is itself recorded. Your own recent activity stays in the Console; the lead
        workflow does not need this page.
      </AccessDenied>
    </PageShell>
  );
}
