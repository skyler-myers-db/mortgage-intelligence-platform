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
 * receipt unavailable" state that still shows the audit id.
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
}

export interface DecisionReceiptProps {
  auditEventId: string;
  /** Plays the one-shot stagger reveal for a decision made in this view. */
  reveal?: boolean;
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

function staggerIndex(index: number): CSSProperties {
  return { '--receipt-i': index } as CSSProperties;
}

export function DecisionReceipt({
  auditEventId,
  reveal = false,
  compact = false,
  score = null,
  className = '',
}: DecisionReceiptProps) {
  const { canAccessAdmin } = useApp();
  const titleId = useId();
  const cardRef = useRef<HTMLElement | null>(null);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');

  const query = useQuery({
    queryKey: queryKeys.auditReceipt(auditEventId),
    queryFn: ({ signal }) => api.auditReceipt(auditEventId, signal),
    staleTime: Infinity,
    retry: false,
  });

  useEffect(() => {
    if (copyState === 'idle') return;
    const timer = window.setTimeout(() => setCopyState('idle'), 2000);
    return () => window.clearTimeout(timer);
  }, [copyState]);

  const onCopy = async () => {
    setCopyState((await copyAuditId(auditEventId)) ? 'copied' : 'failed');
  };
  const copyLabel = copyState === 'copied'
    ? DECISION_RECEIPT_COPY.copied
    : copyState === 'failed'
      ? DECISION_RECEIPT_COPY.copyFailed
      : DECISION_RECEIPT_COPY.copy;
  const blockClass = ['surface', 'decision-receipt', compact ? 'decision-receipt--compact' : '', className]
    .filter(Boolean)
    .join(' ');

  if (query.isPending) {
    return (
      <section
        className={`${blockClass} decision-receipt--pending`}
        role="status"
        aria-busy="true"
        aria-live="polite"
        data-testid="decision-receipt-pending"
      >
        <div className="surface__hdr">
          <Icon name="audit" size={14} className="icon-accent" />
          <div className="h-4">{DECISION_RECEIPT_COPY.recording}</div>
          <span className="decision-receipt__hdr-note">{DECISION_RECEIPT_COPY.recordingNote}</span>
        </div>
        <div className="surface__body decision-receipt__skeleton">
          <Skeleton width="42%" />
          <Skeleton width="68%" />
          <Skeleton width="55%" />
        </div>
      </section>
    );
  }

  if (query.isError || !query.data) {
    const status = query.error instanceof ApiError ? query.error.status : null;
    const scoped = status === 403 || status === 404;
    const message = query.error instanceof Error ? query.error.message : 'unreachable';
    return (
      <section
        className={`${blockClass} decision-receipt--unavailable`}
        role="status"
        aria-labelledby={titleId}
        data-testid="decision-receipt-unavailable"
      >
        <div className="surface__hdr">
          <Chip variant="neutral" icon="shield">{DECISION_RECEIPT_COPY.recorded}</Chip>
          <div className="h-4" id={titleId}>{DECISION_RECEIPT_COPY.unavailableTitle}</div>
        </div>
        <div className="surface__body">
          <p className="muted fs-12 flush">
            {scoped ? DECISION_RECEIPT_COPY.unavailableScoped : `${DECISION_RECEIPT_COPY.unavailableError} ${message}`}
          </p>
          <div className="decision-receipt__ids" data-testid="decision-receipt-audit-id">
            audit event {auditEventId}
          </div>
        </div>
        <div className="surface__ft">
          <div className="decision-receipt__actions">
            <Button size="sm" icon="doc" onClick={() => void onCopy()}>{copyLabel}</Button>
            {!scoped && (
              <Button size="sm" onClick={() => void query.refetch()}>{DECISION_RECEIPT_COPY.retry}</Button>
            )}
          </div>
        </div>
      </section>
    );
  }

  const receipt = query.data;
  const chip = decisionChip(receipt.decision as DecisionOutcome);
  const rows = receiptRows(receipt);
  const evidenceIndex = rows.length;
  const scoreIndex = evidenceIndex + 1;
  const footerIndex = scoreIndex + (score ? 1 : 0);

  return (
    <section
      ref={cardRef}
      className={`${blockClass} decision-receipt--${receipt.decision}${reveal ? ' decision-receipt--reveal' : ''}`}
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
          <Button size="sm" icon="doc" onClick={() => void onCopy()} aria-label={`${DECISION_RECEIPT_COPY.copy} ${receipt.audit_event_id}`}>
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
  );
}
