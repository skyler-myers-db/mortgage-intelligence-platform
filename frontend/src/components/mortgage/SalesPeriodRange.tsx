import { useState, type FocusEvent, type KeyboardEvent } from 'react';
import { MAX_PERIOD_DAYS, customRangeIssue, type CustomRangeIssue } from '../../routes/analytics.sales-ops.period';
import { Field } from '../ui/Field';

/**
 * The Sales ops custom range (2026-09-21 audit critic-10, item 6a): two
 * Field-wrapped native date inputs and an Apply button. Lazy (React.lazy from
 * routes/analytics.sales-ops.tsx) so Field and its stylesheet load only when a
 * manager picks Custom, never with the analytics route.
 *
 * The fields hold a DRAFT, and nothing is applied from `input` or `change`:
 * Chromium fires a date input's `change` on every keyboard edit (each ArrowUp
 * on a segment, each digit of a typed year: 0002-, 0020-, 0202-, 2026-). The
 * draft is applied by Enter in either field, by Apply, or when focus leaves
 * the range (not when it moves between the two fields, and not when the
 * window or the native picker takes it). The inputs are never re-keyed, so
 * focus stays where it is across an apply. A draft that cannot be read (a
 * partly typed year, a future day, a backwards or over-92-day range) stays in
 * the fields with an inline error and never reaches the URL.
 * deviation:sales-manager-period.
 */

type Edge = 'from' | 'to';
type Range = Record<Edge, string>;

const ISSUE_COPY: Record<CustomRangeIssue, string> = {
  from_incomplete: 'Enter a complete date.',
  to_incomplete: 'Enter a complete date.',
  from_future: 'Pick today or an earlier day.',
  to_future: 'Pick today or an earlier day.',
  reversed: 'From must be on or before To.',
  too_long: `A custom range covers at most ${MAX_PERIOD_DAYS} days.`,
};

/** The field an issue is shown on: its own edge, else the field last edited. */
function issueEdge(issue: CustomRangeIssue, edited: Edge): Edge {
  if (issue.startsWith('from_')) return 'from';
  if (issue.startsWith('to_')) return 'to';
  return edited;
}

export interface SalesPeriodRangeProps {
  from: string;
  to: string;
  /** Today, the latest day either input accepts. */
  max: string;
  onApply: (range: { from: string; to: string }) => void;
}

export default function SalesPeriodRange({ from, to, max, onApply }: SalesPeriodRangeProps) {
  const [shown, setShown] = useState<Range>({ from, to });
  const [draft, setDraft] = useState<Range>({ from, to });
  const [edited, setEdited] = useState<Edge>('from');
  const [checked, setChecked] = useState(false);
  // A range that arrives from the URL (an apply, Back, a shared link)
  // replaces the draft in place: the inputs stay mounted and keep focus.
  if (shown.from !== from || shown.to !== to) {
    setShown({ from, to });
    setDraft({ from, to });
    setChecked(false);
  }
  const issue = checked ? customRangeIssue(draft.from, draft.to) : null;
  const errorOn = issue ? issueEdge(issue, edited) : null;

  const apply = () => {
    const unchanged = draft.from === from && draft.to === to;
    const refused = !unchanged && customRangeIssue(draft.from, draft.to) !== null;
    setChecked(refused);
    if (!unchanged && !refused) onApply(draft);
  };
  const leave = (event: FocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget;
    if (next instanceof Node && event.currentTarget.contains(next)) return;
    if (document.hasFocus()) apply();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') apply();
  };
  const field = (edge: Edge, label: string) => (
    <Field label={label} className="sales-ops-period__date" error={issue && errorOn === edge ? ISSUE_COPY[issue] : null}>
      {(control) => (
        <input
          {...control}
          type="date"
          className="form-input"
          value={draft[edge]}
          max={max}
          onChange={(event) => {
            const value = event.currentTarget.value;
            setDraft((current) => ({ ...current, [edge]: value }));
            setEdited(edge);
          }}
          onKeyDown={onKeyDown}
        />
      )}
    </Field>
  );

  return (
    <div className="sales-ops-period__range" role="group" aria-label="Custom range" onBlur={leave}>
      {field('from', 'From')}
      {field('to', 'To')}
      <button type="button" className="btn btn--default btn--sm" onClick={apply}>
        Apply
      </button>
    </div>
  );
}
