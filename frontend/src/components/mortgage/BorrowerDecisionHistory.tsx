/**
 * BorrowerDecisionHistory — one borrower's governed decisions for the
 * working team (audit flow-04 phase 2 / tables-10, D-audit-reads-c2), on
 * Borrower 360 (`surface`) and, collapsed, on the Offer (`disclosure`).
 *
 * The prototype's audit rows (design_files/Module 0 Prototype.html:794-817 and
 * 1511-1522): `.audit` with `.audit__time`, a toned `.audit__ico`, and
 * `.audit__what` / `.audit__who`. The label names the outcome, so the tone is
 * never colour-only. deviation:borrower-decision-history — the list is
 * in-flow (`.audit-panel--flow`), never a scroll region, with rows after the
 * eighth behind "Show all N decisions"; the prototype's panel is a 260px
 * scroller, which axe flags as a scrollable region without a focus stop.
 *
 * The read is audit-free and runs on mount only: never on hover or idle,
 * never polled or prefetched. A 403 (outside the working team) renders
 * nothing; any other failure speaks describeApiError, with Retry. A row's
 * Receipt (own decisions, or an admin / auditor) mounts the existing
 * DecisionReceipt on that explicit click only; a cross-actor receipt read is
 * itself recorded server-side.
 */
import { useId, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError } from '../../lib/apiTransport';
import {
  borrowerDecisionsQuery,
  type BorrowerDecisionEvent,
  type BorrowerDecisionHistoryResponse,
} from '../../lib/apiClients/borrowerDecisions';
import { decisionTone } from '../../lib/auditEventPresentation';
import { describeApiError } from '../../lib/describeApiError';
import { formatDateTimeShort, isoDateTimeAttr } from '../../lib/time';
import { Icon, type IconName } from '../Icon';
import { Button, SurfaceTitle } from '../Primitives';
import { decisionWhat, decisionWho, receiptDecision } from './BorrowerDecisionHistory.labels';
import { lazyModule, useLazyModule } from './useLazyModule';
import './BorrowerDecisionHistory.css';

// The receipt loads on its first open, as offer-orchestrator.decision does.
const RECEIPT_CHUNK = lazyModule(() => import('./DecisionReceipt'));
const VISIBLE_ROWS = 8;

const ICON_BY_OUTCOME: Record<BorrowerDecisionEvent['outcome'], IconName> = {
  approved: 'check',
  rejected: 'cross',
  revoked: 'cross',
  requested: 'send',
  assigned: 'user',
  unassigned: 'user',
  distributed: 'flow',
  status_changed: 'flow',
  disposition: 'chat',
  outcome: 'target',
  activation: 'export',
  contact_blocked: 'shield',
};

export interface BorrowerDecisionHistoryProps {
  borrowerId: string;
  variant: 'surface' | 'disclosure';
}

function DecisionRow({ item }: { item: BorrowerDecisionEvent }) {
  const [receiptOpen, setReceiptOpen] = useState(false);
  const receiptId = useId();
  const decision = receiptDecision(item);
  const receipt = useLazyModule(RECEIPT_CHUNK, receiptOpen);
  const DecisionReceipt = receipt.module?.DecisionReceipt;
  const offersReceipt = item.receipt_available && decision !== null;
  return (
    <li className="audit">
      <time className="audit__time mono" dateTime={isoDateTimeAttr(item.occurred_at) ?? undefined}>
        {formatDateTimeShort(item.occurred_at)}
      </time>
      <div className={`audit__ico ${decisionTone(item.outcome)}`} aria-hidden="true">
        <Icon name={ICON_BY_OUTCOME[item.outcome]} size={11} />
      </div>
      <div className="audit__body">
        <div className="audit__what">{decisionWhat(item)}</div>
        <div className="audit__who">{decisionWho(item)}</div>
      </div>
      {offersReceipt && (
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={receiptOpen}
          aria-controls={receiptId}
          onClick={() => setReceiptOpen((open) => !open)}
        >
          Receipt
        </Button>
      )}
      {offersReceipt && (
        <div id={receiptId} className="audit__receipt" hidden={!receiptOpen}>
          {receiptOpen && DecisionReceipt && decision && (
            <DecisionReceipt auditEventId={item.audit_event_id} decision={decision} compact headingLevel={3} />
          )}
          {receiptOpen && receipt.failed && (
            <p className="audit-panel__message">The receipt could not load.</p>
          )}
        </div>
      )}
    </li>
  );
}

function DecisionList({ history }: { history: BorrowerDecisionHistoryResponse }) {
  const [showAll, setShowAll] = useState(false);
  const listId = useId();
  if (history.items.length === 0) {
    return <p className="audit-panel__message">No decisions recorded for this borrower yet.</p>;
  }
  const rows = showAll ? history.items : history.items.slice(0, VISIBLE_ROWS);
  const hidden = history.items.length > VISIBLE_ROWS;
  return (
    <>
      <ol id={listId} aria-label="Decision history" className="audit-panel audit-panel--flow">
        {rows.map((item) => <DecisionRow key={item.audit_event_id} item={item} />)}
      </ol>
      {(hidden || history.truncated) && (
        <div className="surface__ft surface__ft--wrap">
          {hidden && (
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={showAll}
              aria-controls={listId}
              onClick={() => setShowAll((all) => !all)}
            >
              {showAll ? `Show the latest ${VISIBLE_ROWS}` : `Show all ${history.items.length} decisions`}
            </Button>
          )}
          {history.truncated && <span className="muted fs-12">Showing the latest 50</span>}
        </div>
      )}
    </>
  );
}

function HistoryBody({ query }: { query: ReturnType<typeof useBorrowerDecisions> }) {
  if (query.isPending) {
    return <p className="audit-panel__message">Loading decisions…</p>;
  }
  if (query.isError) {
    const failure = describeApiError(query.error, { subject: 'the decision history' });
    return (
      <div className="audit-panel__pad stack-sm" role="status">
        <p className="audit-panel__message audit-panel__message--danger flush">{failure.title}</p>
        <Button size="sm" onClick={() => void query.refetch()}>
          Retry
        </Button>
      </div>
    );
  }
  return <DecisionList history={query.data} />;
}

function useBorrowerDecisions(borrowerId: string) {
  return useQuery(borrowerDecisionsQuery(borrowerId));
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

export function BorrowerDecisionHistory({ borrowerId, variant }: BorrowerDecisionHistoryProps) {
  const query = useBorrowerDecisions(borrowerId);
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  // Outside the working team: the section is not shown at all.
  if (isForbidden(query.error)) return null;
  if (variant === 'surface') {
    return (
      <div className="surface" data-testid="borrower-decision-history">
        <div className="surface__hdr">
          <Icon name="audit" size={14} className="icon-accent" />
          <SurfaceTitle>Decision history</SurfaceTitle>
        </div>
        <HistoryBody query={query} />
      </div>
    );
  }
  const count = query.data ? (query.data.truncated ? '50+' : String(query.data.items.length)) : null;
  return (
    <div className="surface" data-testid="offer-prior-decisions">
      <button
        type="button"
        className="surface__hdr appearance-toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="appearance-toggle__side">
          <Icon name="audit" size={14} className="icon-accent" />
          {/* A span, not a SurfaceTitle: no heading inside a <button> (a11y-03). */}
          <span className="h-4">{count === null ? 'Prior decisions' : `Prior decisions (${count})`}</span>
        </span>
        <span className="appearance-toggle__side">
          <Icon name={open ? 'up' : 'down'} size={12} />
        </span>
      </button>
      <div id={bodyId} hidden={!open}>
        {open && <HistoryBody query={query} />}
      </div>
    </div>
  );
}
