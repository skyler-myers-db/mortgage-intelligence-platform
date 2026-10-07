import { Link } from 'react-router';
import { auditEventHref } from '../../lib/auditLinks';
import { useAuditLedgerAccess } from '../../lib/sessionQuery';

/**
 * The confirmation of a governed Genie action, with its audit event id
 * (audit 2026-09-21 `flow-04`, the Genie-confirmation half): exactly
 * `{message} Audit event <id>.`, the text genieActionConfirmation speaks.
 * For administrators and auditors the id links to that one ledger row
 * (lib/auditLinks); for everyone else it stays mono text, since the ledger
 * route would answer with its access-denied surface. A pending or errored
 * session read is no access. Inline text only: the caller owns the block it
 * sits in (the panel's bubble paragraph, the route's status callout).
 */
export function GenieActionReceipt({ message, auditEventId }: { message: string; auditEventId: string }) {
  const canReadLedger = useAuditLedgerAccess();
  return (
    <>
      {message} Audit event{' '}
      {canReadLedger ? (
        <Link className="mono" to={auditEventHref(auditEventId)}>
          {auditEventId}
        </Link>
      ) : (
        <span className="mono">{auditEventId}</span>
      )}
      .
    </>
  );
}
