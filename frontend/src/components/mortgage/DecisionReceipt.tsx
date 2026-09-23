/**
 * DecisionReceipt — the approval moment ends in the real Lakebase ledger row.
 *
 * After an approve / reject POST resolves with an audit id, this reads the
 * row BACK (`GET /api/audit/receipt/{id}`) and only then renders the ledger
 * card: decision, borrower, offer, channel, campaign, the exact copy hash the
 * approval bound, approver, request / correlation ids, timestamp, and an
 * EvidenceChip per Unity Catalog asset the decision cited. Pessimistic by
 * construction: nothing on the card comes from the POST body; while the
 * read-back is in flight the card is a "Recording decision…" skeleton, and a
 * refused read (another approver's row, no admin) is a neutral "Recorded;
 * receipt unavailable" state that still shows the audit id. A 404 (no
 * decision row for the id the write returned) is NOT shown as recorded: it
 * says the ledger did not confirm the row and keeps "Retry read-back".
 * Whenever the read-back is unavailable, the outcome the caller already
 * knows (the resolved POST or the durable lifecycle row) stays on the card,
 * so the page never stops saying what was decided.
 *
 * BEM block `.decision-receipt` (frontend/src/design-system/components/
 * 19-decision-receipt.css), an extension of the prototype's `.surface`.
 */
import { useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { api, ApiError } from '../../lib/api';
import type { DecisionOutcome, DecisionReceipt as DecisionReceiptPayload } from '../../lib/apiTypes';
import { descriptorFor } from '../../lib/drawerSources';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { queryKeys } from '../../lib/queryKeys';
import { formatTimestamp } from '../../lib/time';
import { useApp } from '../AppContext';
import { Icon } from '../Icon';
import { Button, Chip, EvidenceChip } from '../Primitives';
import { Skeleton } from '../ui/Skeleton';
import { ConfidenceMeter } from './ConfidenceMeter';
import { ScoreBadge } from './ScoreBadge';
import { DECISION_RECEIPT_COPY, decisionChip, humanizeReasonCode, receiptChannelLabel } from './DecisionReceipt.copy';
import { copyAuditId, printReceipt } from './DecisionReceipt.actions';

/** What a queue row remembers about the decision it just made. */
export interface LeadDecisionReceipt {
  auditEventId: string | null;
  decision: 'approved' | 'rejected';
  /** The one-shot reveal already played for this decision (motion-06). */
  revealed?: boolean;
  /** Records that the reveal played, so a collapse + re-expand shows the receipt finished. */
  markRevealed?: () => void;
}

export interface DecisionReceiptProps {
  auditEventId: string;
  /**
   * The outcome the caller already knows authoritatively: the resolved
   * approve / reject POST, or the durable lifecycle row. Shown beside an
   * unavailable read-back (403 / 404 / error), so the page always states
   * the decision; a confirmed receipt shows the ledger row's own decision.
   */
  decision?: DecisionOutcome;
  /**
   * The audit id came from an approve / reject write made in this view, not
   * from a durable decision record. Words the pending state "Recording
   * decision…" and the not-found state as a write the ledger did not
   * confirm, and announces the outcome. A passive read of an earlier
   * decision reads "Reading decision receipt…" and stays quiet.
   */
  decidedHere?: boolean;
  /** Plays the one-shot stagger reveal for a decision made in this view. */
  reveal?: boolean;
  /** Called once the revealed receipt has rendered from the read-back. */
  onRevealed?: () => void;
  /** Two-column ledger for the queue's expanded row. */
  compact?: boolean;
  /** The lead payload's score line, when the caller has it. */
  score?: { opportunityScore: number; confidence: number } | null;
  className?: string;
}

export function auditExplorerHref(auditEventId: string): string {
  return `/admin-config?audit_event_id=${encodeURIComponent(auditEventId)}#audit`;
}

interface ReceiptRow {
  key: string;
  label: string;
  value: string;
  mono?: boolean;
  title?: string;
}

function receiptRows(receipt: DecisionReceiptPayload): ReceiptRow[] {
  const rows: ReceiptRow[] = [
    { key: 'audit', label: 'Audit event', value: receipt.audit_event_id, mono: true },
  ];
  if (receipt.borrower_id) rows.push({ key: 'borrower', label: 'Borrower', value: receipt.borrower_id, mono: true });
  if (receipt.offer_code || receipt.offer_label) {
    rows.push({
      key: 'offer',
      label: 'Offer',
      value: offerDisplayLabel(receipt.offer_code, receipt.offer_label ?? undefined),
    });
  }
  if (receipt.channel) rows.push({ key: 'channel', label: 'Channel', value: receiptChannelLabel(receipt.channel) });
  if (receipt.campaign_id) {
    rows.push({
      key: 'campaign',
      label: 'Campaign · variant',
      value: `${receipt.campaign_id.slice(0, 12)} · ${receipt.variant_name ?? 'variant unnamed'}`,
      mono: true,
      title: receipt.campaign_id,
    });
  }
  if (receipt.rationale_code) {
    rows.push({ key: 'reason', label: 'Reason code', value: humanizeReasonCode(receipt.rationale_code) });
  }
  if (receipt.copy_hash) {
    rows.push({
      key: 'copy-hash',
      label: 'Copy hash (bound at approval)',
      value: receipt.copy_hash,
      mono: true,
      title: receipt.copy_hash,
    });
  }
  if (receipt.copy_generation_id) {
    rows.push({ key: 'copy-generation', label: 'Copy generation', value: receipt.copy_generation_id, mono: true });
  }
  rows.push({ key: 'approver', label: 'Approver', value: receipt.approver });
  if (receipt.request_id) rows.push({ key: 'request', label: 'Request id', value: receipt.request_id, mono: true });
  if (receipt.correlation_id) {
    rows.push({ key: 'correlation', label: 'Correlation id', value: receipt.correlation_id, mono: true });
  }
  rows.push({ key: 'recorded', label: 'Recorded at', value: formatTimestamp(receipt.created_at) });
  if (receipt.approval_id) rows.push({ key: 'approval', label: 'Decision record', value: receipt.approval_id, mono: true });
  return rows;
}

/** Which card the receipt is showing: the skeleton, an unavailable state, or the read-back. */
type ReceiptPhase = 'pending' | 'unavailable' | 'read-back';

/** Why the read-back did not return a receipt. */
type UnavailableState = 'forbidden' | 'not-found' | 'error';

function unavailableState(error: unknown): UnavailableState {
  const status = error instanceof ApiError ? error.status : null;
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not-found';
  return 'error';
}

/**
 * One polite live region per receipt. The section beside it changes content
 * and role between the pending, unavailable and read-back states, so the
 * announcement lives outside it in one node that stays mounted and is
 * updated in place: a screen reader hears the decision once the ledger
 * confirms it (flow-03). Only a decision made in this view is announced; a
 * durable receipt read on page load or from "Latest decision" is not news.
 */
function ReceiptAnnouncement({ message }: { message: string }) {
  return (
    <span className="sr-only" role="status" aria-live="polite" aria-atomic="true" data-testid="decision-receipt-announcement">
      {message}
    </span>
  );
}

function staggerIndex(index: number): CSSProperties {
  return { '--receipt-i': index } as CSSProperties;
}

export function DecisionReceipt({
  auditEventId,
  decision,
  decidedHere = false,
  reveal = false,
  onRevealed,
  compact = false,
  score = null,
  className = '',
}: DecisionReceiptProps) {
  const { canAccessAdmin } = useApp();
  const titleId = useId();
  const cardRef = useRef<HTMLElement | null>(null);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  // The copy result is announced through the receipt's one live region.
  // It is tied to the audit id and the card state it was made in, so a
  // later state (a retried read-back that now succeeds) announces itself.
  const [copyNotice, setCopyNotice] = useState<{ auditEventId: string; phase: ReceiptPhase; text: string } | null>(null);

  const query = useQuery({
    queryKey: queryKeys.auditReceipt(auditEventId),
    queryFn: ({ signal }) => api.auditReceipt(auditEventId, signal),
    staleTime: Infinity,
    retry: false,
  });

  // motion-06: the reveal is one-shot. It is latched per audit id at first
  // render, so a parent that records "revealed" (onRevealed) while the
  // stagger plays cannot cut it short; the next mount (a collapsed and
  // re-expanded queue row) gets reveal=false and renders the receipt finished.
  const [revealLatch, setRevealLatch] = useState({ auditEventId, reveal });
  if (revealLatch.auditEventId !== auditEventId) setRevealLatch({ auditEventId, reveal });
  const playReveal = revealLatch.auditEventId === auditEventId ? revealLatch.reveal : reveal;
  const readBack = Boolean(query.data);
  useEffect(() => {
    if (playReveal && readBack) onRevealed?.();
  }, [playReveal, readBack, onRevealed]);

  useEffect(() => {
    if (copyState === 'idle') return;
    const timer = window.setTimeout(() => {
      setCopyState('idle');
      // Quiet, not reverted: falling back to the decision text here would
      // announce the decision again. The next copy announces afresh.
      setCopyNotice((notice) => (notice ? { ...notice, text: '' } : notice));
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [copyState]);

  const phase: ReceiptPhase = query.isPending ? 'pending' : query.data ? 'read-back' : 'unavailable';
  const onCopy = async () => {
    const copied = await copyAuditId(auditEventId);
    setCopyState(copied ? 'copied' : 'failed');
    setCopyNotice({
      auditEventId,
      phase,
      text: copied ? DECISION_RECEIPT_COPY.copiedNotice : DECISION_RECEIPT_COPY.copyFailedNotice,
    });
  };
  const copyLabel = copyState === 'copied'
    ? DECISION_RECEIPT_COPY.copied
    : copyState === 'failed'
      ? DECISION_RECEIPT_COPY.copyFailed
      : DECISION_RECEIPT_COPY.copy;
  const blockClass = ['surface', 'decision-receipt', compact ? 'decision-receipt--compact' : '', className]
    .filter(Boolean)
    .join(' ');
  const announce = (message: string) => {
    if (copyNotice && copyNotice.auditEventId === auditEventId && copyNotice.phase === phase) return copyNotice.text;
    return decidedHere ? message : '';
  };

  if (query.isPending) {
    // A passive read is not a write: it never says "Recording decision…".
    const pendingTitle = decidedHere ? DECISION_RECEIPT_COPY.recording : DECISION_RECEIPT_COPY.reading;
    const pendingNote = decidedHere ? DECISION_RECEIPT_COPY.recordingNote : DECISION_RECEIPT_COPY.readingNote;
    return (
      <>
        <ReceiptAnnouncement message={announce(pendingNote)} />
        <section
          className={`${blockClass} decision-receipt--pending`}
          aria-busy="true"
          data-testid="decision-receipt-pending"
        >
          <div className="surface__hdr">
            <Icon name="audit" size={14} className="icon-accent" />
            <div className="h-4">{pendingTitle}</div>
            <span className="decision-receipt__hdr-note">{pendingNote}</span>
          </div>
          <div className="surface__body decision-receipt__skeleton">
            <Skeleton width="42%" />
            <Skeleton width="68%" />
            <Skeleton width="55%" />
          </div>
        </section>
      </>
    );
  }

  if (query.isError || !query.data) {
    const state = unavailableState(query.error);
    const message = query.error instanceof Error ? query.error.message : 'unreachable';
    // Only a 403 is "in the ledger, not yours to read". A 404 means the
    // read-back found no decision row for the id the write returned: the
    // integrity failure the read-back exists to catch, so it claims nothing
    // about the ledger and keeps the retry.
    const notFound = state === 'not-found';
    const explanation = state === 'forbidden'
      ? DECISION_RECEIPT_COPY.unavailableScoped
      : notFound
        ? (decidedHere ? DECISION_RECEIPT_COPY.unavailableNotFound : DECISION_RECEIPT_COPY.unavailableNotFoundRecord)
        : `${DECISION_RECEIPT_COPY.unavailableError} ${message}`;
    const title = notFound ? DECISION_RECEIPT_COPY.notFoundTitle : DECISION_RECEIPT_COPY.unavailableTitle;
    // The caller's known outcome: without it an unreadable receipt would be
    // the only decision surface and the page would no longer say it.
    const outcome = decision ? decisionChip(decision) : null;
    return (
      <>
        <ReceiptAnnouncement
          message={announce(`${outcome ? `${outcome.label}. ` : ''}${title}, audit event ${auditEventId}`)}
        />
        <section
          className={`${blockClass} decision-receipt--unavailable`}
          aria-labelledby={titleId}
          data-testid="decision-receipt-unavailable"
          data-receipt-state={state}
        >
          <div className="surface__hdr">
            {outcome && (
              <span className="inline-flex" data-testid="decision-receipt-outcome">
                <Chip variant={outcome.variant} icon={outcome.icon}>{outcome.label}</Chip>
              </span>
            )}
            {notFound ? (
              <Chip variant="warning" icon="audit">{DECISION_RECEIPT_COPY.unconfirmed}</Chip>
            ) : (
              <Chip variant="neutral" icon="shield">{DECISION_RECEIPT_COPY.recorded}</Chip>
            )}
            <div className="h-4" id={titleId}>{title}</div>
          </div>
          <div className="surface__body">
            <p className="muted fs-12 flush">{explanation}</p>
            <div className="decision-receipt__ids" data-testid="decision-receipt-audit-id">
              audit event {auditEventId}
            </div>
          </div>
          <div className="surface__ft">
            <div className="decision-receipt__actions">
              <Button size="sm" icon="doc" onClick={() => void onCopy()}>{copyLabel}</Button>
              {state !== 'forbidden' && (
                <Button size="sm" onClick={() => void query.refetch()}>{DECISION_RECEIPT_COPY.retry}</Button>
              )}
            </div>
          </div>
        </section>
      </>
    );
  }

  const receipt = query.data;
  const chip = decisionChip(receipt.decision as DecisionOutcome);
  const rows = receiptRows(receipt);
  const evidenceIndex = rows.length;
  const scoreIndex = evidenceIndex + 1;
  const footerIndex = scoreIndex + (score ? 1 : 0);

  const announcement = `${playReveal ? DECISION_RECEIPT_COPY.announceRecorded : DECISION_RECEIPT_COPY.title}: ${chip.label}, audit event ${receipt.audit_event_id}`;

  return (
    <>
      <ReceiptAnnouncement message={announce(announcement)} />
      <section
        ref={cardRef}
        className={`${blockClass} decision-receipt--${receipt.decision}${playReveal ? ' decision-receipt--reveal' : ''}`}
        aria-labelledby={titleId}
        data-testid="decision-receipt"
        data-audit-event-id={receipt.audit_event_id}
      >
        <div className="surface__hdr">
          <Icon name="audit" size={14} className="icon-accent" />
          <div className="h-4" id={titleId}>{DECISION_RECEIPT_COPY.title}</div>
          <Chip variant={chip.variant} icon={chip.icon}>{chip.label}</Chip>
          <span className="decision-receipt__hdr-note">{DECISION_RECEIPT_COPY.readBackNote}</span>
        </div>
        <div className="surface__body">
          <dl className="decision-receipt__grid">
            {rows.map((row, index) => (
              <div key={row.key} className="decision-receipt__row" style={staggerIndex(index)}>
                <dt className="field__label">{row.label}</dt>
                <dd
                  className={`field__value${row.mono ? ' decision-receipt__value--mono' : ''}`}
                  title={row.title}
                  data-receipt-field={row.key}
                >
                  {row.value}
                </dd>
              </div>
            ))}
          </dl>
          <div className="decision-receipt__section" style={staggerIndex(evidenceIndex)}>
            <div className="eyebrow mb-2">{DECISION_RECEIPT_COPY.evidence}</div>
            <p className="muted fs-11 flush mb-2" data-testid="decision-receipt-evidence-note">
              {DECISION_RECEIPT_COPY.evidenceAssetsNote}
            </p>
            <div className="chip-row" data-testid="decision-receipt-evidence">
              {receipt.evidence_assets.map((asset) => {
                const source = descriptorFor(asset);
                return (
                  <EvidenceChip key={asset} source={source}>{source.title}</EvidenceChip>
                );
              })}
              {receipt.evidence_assets.length === 0 && (
                <span className="muted fs-12">{DECISION_RECEIPT_COPY.noEvidenceAssets}</span>
              )}
            </div>
            {receipt.evidence_ids.length > 0 && (
              <div className="decision-receipt__ids">
                evidence {receipt.evidence_ids.join(' · ')}
              </div>
            )}
          </div>
          {score && (
            <div className="decision-receipt__section" style={staggerIndex(scoreIndex)}>
              <div className="eyebrow mb-2">{DECISION_RECEIPT_COPY.scoreAtDecision}</div>
              <div className="decision-receipt__score" data-testid="decision-receipt-score">
                <ScoreBadge value={score.opportunityScore} />
                <ConfidenceMeter value={score.confidence} compact />
                <span className="muted fs-11">{DECISION_RECEIPT_COPY.scoreNote}</span>
              </div>
            </div>
          )}
        </div>
        <div className="surface__ft" style={staggerIndex(footerIndex)}>
          <div className="decision-receipt__actions">
            {/* WCAG 2.5.3: the accessible name starts with the visible label in every copy state. */}
            <Button size="sm" icon="doc" onClick={() => void onCopy()} aria-label={`${copyLabel} ${receipt.audit_event_id}`}>
              {copyLabel}
            </Button>
            <Button size="sm" icon="export" onClick={() => printReceipt(cardRef.current)}>
              {DECISION_RECEIPT_COPY.print}
            </Button>
            {canAccessAdmin && (
              <Link
                className="btn btn--sm decision-receipt__explorer"
                to={auditExplorerHref(receipt.audit_event_id)}
                data-testid="decision-receipt-explorer-link"
              >
                {DECISION_RECEIPT_COPY.openExplorer}
              </Link>
            )}
          </div>
        </div>
      </section>
    </>
  );
}
