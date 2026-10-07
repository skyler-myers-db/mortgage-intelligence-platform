import { useEffect, useId, useRef, useState } from 'react';
import type { LeadSummary } from '../../types';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import { isAbortError } from '../../lib/api';
import { formatCount } from '../../lib/formatters';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { Button, Chip } from '../Primitives';
import { offerCounts } from './LeadBulkApproveReview.counts';
import { bulkSampleCoverage } from './LeadBulkApproveReview.coverage';
import { stratifiedSampleIds } from './LeadBulkApproveReview.sampler';
import './LeadBulkApproveReview.css';

// A bulk run's progress and report ride this lazy chunk too (tables-07),
// and so does the bulk reject gate: none of it is in the LeadTable chunk.
export { LeadBulkRunProgress, LeadBulkRunResult } from './LeadBulkRunStatus';
export { LeadBulkRejectGate } from './LeadBulkRejectGate';
export { LeadBulkCampaignHandoff } from './LeadBulkCampaignHandoff';
export { bulkCanaryNotice } from './LeadBulkRunStatus.copy';
export { canaryNotice } from './LeadTable.canary';
export { runBulkApprove, runBulkReject } from './leadBulkDecisions';
export { offerCounts } from './LeadBulkApproveReview.counts';

/**
 * The bulk approve gate's review block (audit states-06, flow-03,
 * D-approval-flow-a1; deviation:bulk-approve-arming: it reports the
 * coverage that arms Approve): shown under the required shared rationale
 * while the gate is open for two or more rows.
 *
 *   - Count by offer: what the run would approve, per primary offer.
 *   - Sampled drafts ONLY behind an explicit "Preview k sample drafts (one
 *     per offer)" button whose note says it generates audited drafts: every
 *     draft writes a DRAFT_OUTREACH audit row, so nothing drafts on open or
 *     on a selection change. The samples are stratified: the first row of
 *     each offer, then top-ups (LeadBulkApproveReview.coverage). A selection
 *     change that adds an offer with no sample offers "Preview 1 more sample
 *     (<offer>)", which drafts only that offer's first row, on click. A
 *     failed sample leaves its offer uncovered until it is retried or
 *     deselected.
 *   - The sampled rows are approved with exactly the copy shown
 *     (`onSamplesChange` hands the drafts to the run, review_mode
 *     'bulk_sample'); the others are approved under the shared rationale
 *     (review_mode 'bulk_cohort'), and the toolbar arms Approve only once
 *     every offer in the run has a sample (`onCoverageChange`).
 *
 * The whole block unmounts with the gate, which discards the samples.
 */
export const BULK_SAMPLE_SIZE = 3;

interface BulkSample {
  borrowerId: string;
  status: 'loading' | 'ready' | 'error';
  draft: OutreachDraftResult | null;
  error: string | null;
}

export interface BulkSampleCoverageChange {
  complete: boolean;
  missingOfferLabels: string[];
}

export interface LeadBulkApproveReviewProps {
  /** Selected rows the run would approve, in table order. */
  leads: readonly LeadSummary[];
  canStartApproval: () => boolean;
  draftForApproval: (borrowerId: string, signal?: AbortSignal) => Promise<OutreachDraftResult>;
  onSamplesChange: (drafts: ReadonlyMap<string, OutreachDraftResult>) => void;
  /** Whether every offer in the run has a ready sample; sent only when it changes. */
  onCoverageChange?: (coverage: BulkSampleCoverageChange) => void;
}

const NO_COVERAGE: BulkSampleCoverageChange = { complete: false, missingOfferLabels: [] };

export function LeadBulkApproveReview({
  leads,
  canStartApproval,
  draftForApproval,
  onSamplesChange,
  onCoverageChange,
}: LeadBulkApproveReviewProps) {
  const noteId = useId();
  const [samples, setSamples] = useState<BulkSample[]>([]);
  const controllersRef = useRef<Set<AbortController>>(new Set());
  const order = new Map(leads.map((lead, index) => [lead.borrower_id, index]));
  // A sample stays bound only while its row is still in the run.
  const liveSamples = samples
    .filter((sample) => order.has(sample.borrowerId))
    .sort((a, b) => (order.get(a.borrowerId) ?? 0) - (order.get(b.borrowerId) ?? 0));
  const loading = liveSamples.some((sample) => sample.status === 'loading');
  const readyDrafts = new Map<string, OutreachDraftResult>();
  for (const sample of liveSamples) {
    if (sample.status === 'ready' && sample.draft) readyDrafts.set(sample.borrowerId, sample.draft);
  }
  const coverage = bulkSampleCoverage(leads, readyDrafts);
  const labelFor = (borrowerId: string) => {
    const lead = leads[order.get(borrowerId) ?? -1];
    return offerDisplayLabel(lead?.recommended_offer_code, lead?.recommended_offer);
  };
  const missingOfferLabels = coverage.missingSampleIds.map(labelFor);
  // First preview: the stratified samples. After it: only the offers still
  // uncovered (added by a selection change, or a failed sample to retry).
  const firstPreview = liveSamples.length === 0;
  const targets = firstPreview ? stratifiedSampleIds(leads, BULK_SAMPLE_SIZE) : coverage.missingSampleIds;
  const readyKey = [...readyDrafts.entries()]
    .map(([borrowerId, draft]) => `${borrowerId}:${draft.generation_id}`)
    .join('|');
  const coverageKey = `${coverage.complete}|${missingOfferLabels.join('|')}`;

  const onSamplesChangeRef = useRef(onSamplesChange);
  const onCoverageChangeRef = useRef(onCoverageChange);
  useEffect(() => {
    onSamplesChangeRef.current = onSamplesChange;
    onCoverageChangeRef.current = onCoverageChange;
  }, [onSamplesChange, onCoverageChange]);
  useEffect(() => {
    onSamplesChangeRef.current(readyDrafts);
    onCoverageChangeRef.current?.({ complete: coverage.complete, missingOfferLabels });
    // readyDrafts and the coverage are derived from these keys' inputs; the
    // keys are their identity, so a keystroke elsewhere reports nothing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readyKey, coverageKey]);
  useEffect(() => {
    const controllers = controllersRef.current;
    return () => {
      for (const ctrl of controllers) ctrl.abort();
      onSamplesChangeRef.current(new Map());
      onCoverageChangeRef.current?.(NO_COVERAGE);
    };
  }, []);

  function preview() {
    if (loading || targets.length === 0 || !canStartApproval()) return;
    const ctrl = new AbortController();
    controllersRef.current.add(ctrl);
    const ids = [...targets];
    setSamples((current) => [
      ...current.filter((sample) => !ids.includes(sample.borrowerId)),
      ...ids.map((borrowerId): BulkSample => ({ borrowerId, status: 'loading', draft: null, error: null })),
    ]);
    const settle = (borrowerId: string, patch: Partial<BulkSample>) => {
      setSamples((current) => current.map((sample) => (
        sample.borrowerId === borrowerId ? { ...sample, ...patch } : sample
      )));
    };
    let pending = ids.length;
    const done = () => {
      pending -= 1;
      if (pending === 0) controllersRef.current.delete(ctrl);
    };
    for (const borrowerId of ids) {
      draftForApproval(borrowerId, ctrl.signal)
        .then((draft) => {
          if (!ctrl.signal.aborted) settle(borrowerId, { status: 'ready', draft });
        })
        .catch((err: unknown) => {
          if (ctrl.signal.aborted || isAbortError(err)) return;
          const message = err instanceof Error ? err.message : 'The draft could not be generated.';
          settle(borrowerId, { status: 'error', error: message });
        })
        .finally(done);
    }
  }

  const count = targets.length;
  const buttonLabel = loading
    ? 'Generating drafts…'
    : firstPreview
      ? `Preview ${formatCount(count)} sample draft${count === 1 ? '' : 's'} (one per offer)`
      : count === 1
        ? `Preview 1 more sample (${missingOfferLabels[0]})`
        : `Preview ${formatCount(count)} more samples (one per offer)`;

  return (
    <div className="bulk-actions__review" data-testid="lead-bulk-review">
      <div className="bulk-actions__offers" data-testid="lead-bulk-offer-counts">
        <span className="field__label">By offer</span>
        {offerCounts(leads).map(({ label, count: rows }) => (
          <Chip key={label} variant="neutral">
            {label} <span className="mono num">{formatCount(rows)}</span>
          </Chip>
        ))}
      </div>
      {(loading || count > 0) && (
        <div className="bulk-actions__sampler">
          <Button
            type="button"
            size="sm"
            icon="doc"
            onClick={preview}
            aria-describedby={noteId}
            aria-disabled={loading || undefined}
            data-testid="lead-bulk-preview-samples"
          >
            {buttonLabel}
          </Button>
          <span id={noteId} className="muted fs-12">
            Generates {formatCount(count)} audited draft{count === 1 ? '' : 's'}: each one is recorded in the
            audit log, and those rows are approved with exactly the copy shown.
          </span>
        </div>
      )}
      {liveSamples.length > 0 && (
        <ul className="bulk-actions__samples" aria-label="Sample drafts" data-testid="lead-bulk-samples">
          {liveSamples.map((sample) => (
            <li key={sample.borrowerId} className="bulk-actions__sample" aria-busy={sample.status === 'loading' || undefined}>
              <div className="field__label">
                <span className="mono">{sample.borrowerId}</span> · Email · {labelFor(sample.borrowerId)}
              </div>
              {sample.status === 'loading' && <div className="muted fs-12">Generating the governed draft…</div>}
              {sample.status === 'error' && <div className="text-danger fs-12" role="alert">{sample.error}</div>}
              {sample.status === 'ready' && sample.draft && (
                <>
                  <div className="bulk-actions__sample-subject">{sample.draft.subject}</div>
                  <div className="bulk-actions__sample-body">{sample.draft.body}</div>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
