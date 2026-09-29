import { useEffect, useId, useRef, useState } from 'react';
import type { LeadSummary } from '../../types';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import { isAbortError } from '../../lib/api';
import { formatCount } from '../../lib/formatters';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { Button, Chip } from '../Primitives';
import './LeadBulkApproveReview.css';

// A bulk run's progress and report ride this lazy chunk too (tables-07).
export { LeadBulkRunProgress, LeadBulkRunResult } from './LeadBulkRunStatus';

/**
 * The bulk approve gate's review block (audit states-06): shown under the
 * required shared rationale while the gate is open for two or more rows.
 *
 *   - Count by offer: what the run would approve, per primary offer.
 *   - Sampled drafts ONLY behind an explicit "Preview N sample drafts"
 *     button whose copy says it generates audited drafts: every draft
 *     writes a DRAFT_OUTREACH audit row, so nothing drafts on open. The
 *     sampled rows are then approved with exactly that copy
 *     (`onSamplesChange` hands the drafts to the bulk run).
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

export interface LeadBulkApproveReviewProps {
  /** Selected rows the run would approve, in table order. */
  leads: readonly LeadSummary[];
  canStartApproval: () => boolean;
  draftForApproval: (borrowerId: string, signal?: AbortSignal) => Promise<OutreachDraftResult>;
  onSamplesChange: (drafts: ReadonlyMap<string, OutreachDraftResult>) => void;
}

export function offerCounts(leads: readonly LeadSummary[]): Array<{ label: string; count: number }> {
  const counts = new Map<string, number>();
  for (const lead of leads) {
    const label = offerDisplayLabel(lead.recommended_offer_code, lead.recommended_offer);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

export function LeadBulkApproveReview({
  leads,
  canStartApproval,
  draftForApproval,
  onSamplesChange,
}: LeadBulkApproveReviewProps) {
  const noteId = useId();
  const [samples, setSamples] = useState<BulkSample[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const sampleSize = Math.min(BULK_SAMPLE_SIZE, leads.length);
  const loading = samples.some((sample) => sample.status === 'loading');
  const selectedIds = new Set(leads.map((lead) => lead.borrower_id));
  // A sample stays bound only while its row is still in the run.
  const liveSamples = samples.filter((sample) => selectedIds.has(sample.borrowerId));
  const readyKey = liveSamples
    .filter((sample) => sample.status === 'ready')
    .map((sample) => `${sample.borrowerId}:${sample.draft?.generation_id ?? ''}`)
    .join('|');

  const onSamplesChangeRef = useRef(onSamplesChange);
  useEffect(() => {
    onSamplesChangeRef.current = onSamplesChange;
  }, [onSamplesChange]);
  useEffect(() => {
    const drafts = new Map<string, OutreachDraftResult>();
    for (const sample of liveSamples) {
      if (sample.status === 'ready' && sample.draft) drafts.set(sample.borrowerId, sample.draft);
    }
    onSamplesChangeRef.current(drafts);
    // liveSamples is derived from readyKey's inputs; readyKey is the identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readyKey]);
  useEffect(() => () => {
    abortRef.current?.abort();
    onSamplesChangeRef.current(new Map());
  }, []);

  function preview() {
    if (loading || sampleSize === 0 || !canStartApproval()) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const ids = leads.slice(0, sampleSize).map((lead) => lead.borrower_id);
    setSamples(ids.map((borrowerId) => ({ borrowerId, status: 'loading', draft: null, error: null })));
    for (const borrowerId of ids) {
      draftForApproval(borrowerId, ctrl.signal)
        .then((draft) => {
          if (ctrl.signal.aborted) return;
          setSamples((current) => current.map((sample) => (
            sample.borrowerId === borrowerId ? { ...sample, status: 'ready', draft } : sample
          )));
        })
        .catch((err: unknown) => {
          if (ctrl.signal.aborted || isAbortError(err)) return;
          const message = err instanceof Error ? err.message : 'The draft could not be generated.';
          setSamples((current) => current.map((sample) => (
            sample.borrowerId === borrowerId ? { ...sample, status: 'error', error: message } : sample
          )));
        });
    }
  }

  return (
    <div className="bulk-actions__review" data-testid="lead-bulk-review">
      <div className="bulk-actions__offers" data-testid="lead-bulk-offer-counts">
        <span className="field__label">By offer</span>
        {offerCounts(leads).map(({ label, count }) => (
          <Chip key={label} variant="neutral">
            {label} <span className="mono num">{formatCount(count)}</span>
          </Chip>
        ))}
      </div>
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
          {loading ? 'Generating drafts…' : `Preview ${sampleSize} sample draft${sampleSize === 1 ? '' : 's'}`}
        </Button>
        <span id={noteId} className="muted fs-12">
          Generates {sampleSize} audited draft{sampleSize === 1 ? '' : 's'}: each one is recorded in the
          audit log, and those rows are approved with exactly the copy shown.
        </span>
      </div>
      {liveSamples.length > 0 && (
        <ul className="bulk-actions__samples" aria-label="Sample drafts" data-testid="lead-bulk-samples">
          {liveSamples.map((sample) => (
            <li key={sample.borrowerId} className="bulk-actions__sample" aria-busy={sample.status === 'loading' || undefined}>
              <div className="field__label">
                <span className="mono">{sample.borrowerId}</span> · Email
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
