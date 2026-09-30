/**
 * Score and rate-spread bounds in the More filters panel (audit tables-06,
 * wow-stage-1): four labelled number inputs that write the public
 * `min/max_opportunity_score` and `min/max_rate_spread_bps` params.
 *
 * deviation:lead-queue-range-filters. The prototype's filter bar
 * (design_files/Module 0 Prototype.html:1772-1778) is `.filter` pills only;
 * these are Field-wrapped `.form-input` controls in the inline panel.
 *
 * A value commits ONLY on blur or Enter: one URL write per commit (so one
 * GET /api/leads), none per keystroke and none when the value is unchanged;
 * an empty field clears its param. An out-of-range value clamps and says so
 * in the Field notice; an at-least above its at-most is a Field error and is
 * not written. A Genie cohort or a Growth Agent handoff carries its own
 * thresholds, so the inputs are disabled while either is in the URL.
 */
import { useId, useState, type KeyboardEvent } from 'react';
import { useSearchParams } from 'react-router';
import { Field } from '../components/ui/Field';
import { formatCount } from '../lib/formatters';
import {
  LEAD_BOUND_LIMITS,
  leadBoundDimension,
  leadBoundsBlocked,
  parseLeadBound,
  type LeadBoundFilterKey,
} from './lead-queue.filters';
import './lead-queue.filterTools.css';

interface RangeInputSpec {
  key: LeadBoundFilterKey;
  label: string;
}

export const LEAD_RANGE_INPUTS: readonly RangeInputSpec[] = [
  { key: 'min_opportunity_score', label: 'Score at least' },
  { key: 'max_opportunity_score', label: 'Score at most' },
  { key: 'min_rate_spread_bps', label: 'Rate spread at least (bps)' },
  { key: 'max_rate_spread_bps', label: 'Rate spread at most (bps)' },
];

export const RANGE_INVERTED_ERROR = 'The at-least value must not be above the at-most value.';
export const RANGE_NOT_A_NUMBER_ERROR = 'Enter a whole number.';

export type RangeCommit =
  | { kind: 'write'; next: URLSearchParams; notice: string | null; committed: string }
  | { kind: 'unchanged'; notice: string | null; committed: string }
  | { kind: 'error'; error: string };

function twinOf(key: LeadBoundFilterKey): LeadBoundFilterKey {
  const [side, ...rest] = key.split('_');
  return `${side === 'min' ? 'max' : 'min'}_${rest.join('_')}` as LeadBoundFilterKey;
}

/**
 * What committing `text` into `key` does to `searchParams`. Pure: the
 * component and its tests share it.
 */
export function rangeCommit(searchParams: URLSearchParams, key: LeadBoundFilterKey, text: string): RangeCommit {
  const current = parseLeadBound(key, searchParams.get(key));
  const raw = text.trim();
  let next: number | undefined;
  let notice: string | null = null;
  if (raw !== '') {
    const value = Number(raw);
    if (!Number.isFinite(value)) return { kind: 'error', error: RANGE_NOT_A_NUMBER_ERROR };
    const { min, max } = LEAD_BOUND_LIMITS[leadBoundDimension(key)];
    next = Math.min(max, Math.max(min, Math.round(value)));
    if (next !== value) notice = `Allowed range is ${formatCount(min)} to ${formatCount(max)}; set to ${formatCount(next)}.`;
    const twin = parseLeadBound(twinOf(key), searchParams.get(twinOf(key)));
    const inverted = twin !== undefined && (key.startsWith('min_') ? next > twin : next < twin);
    if (inverted) return { kind: 'error', error: RANGE_INVERTED_ERROR };
  }
  const committed = next === undefined ? '' : String(next);
  if (next === current) return { kind: 'unchanged', notice, committed };
  const params = new URLSearchParams(searchParams);
  if (next === undefined) params.delete(key);
  else params.set(key, committed);
  return { kind: 'write', next: params, notice, committed };
}

export function LeadQueueRangeFilters() {
  const [searchParams, setSearchParams] = useSearchParams();
  const blocked = leadBoundsBlocked(searchParams);
  const hintId = useId();
  return (
    <div className="lead-queue-range" role="group" aria-label="Score and rate spread" data-testid="lead-queue-range-filters">
      {LEAD_RANGE_INPUTS.map((spec) => (
        <LeadQueueRangeInput
          key={spec.key}
          spec={spec}
          searchParams={searchParams}
          onWrite={setSearchParams}
          disabled={blocked}
          blockedHintId={blocked ? hintId : null}
        />
      ))}
      {blocked && (
        <p className="field__hint lead-queue-range__hint" id={hintId}>
          A Genie cohort or a Growth Agent handoff sets its own score and spread thresholds.
        </p>
      )}
    </div>
  );
}

interface RangeMessage {
  notice: string | null;
  error: string | null;
  /** The committed URL value the message describes. */
  against: string;
  /**
   * The URL value before this field's own write, until the write lands: the
   * data router applies setSearchParams in a transition, so for a render the
   * URL can still hold the old value. Null once settled.
   */
  before: string | null;
}

/** The message still valid for `committed`, settling a landed write. */
function messageFor(message: RangeMessage | null, committed: string): RangeMessage | null {
  if (message === null) return null;
  if (message.against === committed) return message.before === null ? message : { ...message, before: null };
  return message.before === committed ? message : null;
}

function LeadQueueRangeInput({
  spec,
  searchParams,
  onWrite,
  disabled,
  blockedHintId,
}: {
  spec: RangeInputSpec;
  searchParams: URLSearchParams;
  onWrite: (next: URLSearchParams) => void;
  disabled: boolean;
  blockedHintId: string | null;
}) {
  const current = parseLeadBound(spec.key, searchParams.get(spec.key));
  const committed = current === undefined ? '' : String(current);
  // What the person is typing, until it commits; a URL change from anywhere
  // else (a removed chip, Back) makes it stale, and the field shows the URL.
  // `landing`: a value this field wrote, shown until the URL holds it.
  const [draft, setDraft] = useState<{ text: string; against: string; landing?: string } | null>(null);
  if (draft?.landing !== undefined && draft.landing === committed) setDraft(null);
  const [message, setMessage] = useState<RangeMessage | null>(null);
  const valid = messageFor(message, committed);
  if (valid !== message) setMessage(valid);
  const text = draft !== null && draft.against === committed ? draft.text : committed;
  const { min, max } = LEAD_BOUND_LIMITS[leadBoundDimension(spec.key)];

  const commit = () => {
    // Nothing typed, typed against a URL that has since moved, or this
    // field's own write still landing: nothing to commit.
    if (draft === null || draft.against !== committed || draft.landing !== undefined) return;
    const outcome = rangeCommit(searchParams, spec.key, draft.text);
    if (outcome.kind === 'error') {
      setMessage({ notice: null, error: outcome.error, against: committed, before: null });
      return;
    }
    setDraft(outcome.kind === 'write' ? { text: outcome.committed, against: committed, landing: outcome.committed } : null);
    setMessage(outcome.notice === null ? null : {
      notice: outcome.notice,
      error: null,
      against: outcome.committed,
      before: outcome.kind === 'write' ? committed : null,
    });
    if (outcome.kind === 'write') onWrite(outcome.next);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    commit();
  };

  return (
    <Field
      className="lead-queue-range__field"
      label={spec.label}
      notice={message?.notice ?? null}
      error={message?.error ?? null}
    >
      {(control) => (
        <input
          {...control}
          aria-describedby={[control['aria-describedby'], blockedHintId].filter(Boolean).join(' ') || undefined}
          className="form-input"
          type="number"
          inputMode="numeric"
          min={min}
          max={max}
          step={1}
          value={text}
          disabled={disabled}
          onChange={(event) => {
            setDraft({ text: event.currentTarget.value, against: committed });
            if (message?.error) setMessage(null);
          }}
          onBlur={commit}
          onKeyDown={onKeyDown}
          data-testid={`lead-queue-range-${spec.key}`}
        />
      )}
    </Field>
  );
}
