import { Fragment, type ReactNode } from 'react';
import { Icon, type IconName } from '../Icon';
import './EmptyState.css';

/**
 * EmptyState — a MEASURED zero, said with its cause and a next step (audit
 * 2026-09-21 `states-v2`). Shown only for a settled, live result of zero
 * rows: never while loading, warming, erroring or showing the previous
 * filters' placeholder rows (AsyncState decides that).
 *
 * Declared prototype extension: design_files/index.html has no empty-state
 * pattern, so `.empty` follows the app's own `.genie-empty` anatomy
 * (06-genie-chat.css) in the prototype's token vocabulary. The CSS is
 * colocated with this lazy component, never in the initial stylesheet.
 */

export type EmptyCause = 'filtered' | 'coverage' | 'intersection' | 'day-zero';

const CAUSES: Readonly<Record<EmptyCause, { title: string; secondary: string | null; icon: IconName }>> = {
  // Verbatim: accessibility_procurement.spec.ts greps this sentence.
  filtered: { title: 'No leads match this filter.', secondary: null, icon: 'search' },
  coverage: {
    title: 'No ZIP-level rollup for this county in the current Cotality data coverage.',
    secondary: null,
    icon: 'pin',
  },
  intersection: {
    title: '0 borrowers sit in every selected segment — a real intersection result from the live query, not an error.',
    secondary: 'Remove a segment chip to widen the cohort.',
    icon: 'layers',
  },
  'day-zero': { title: 'No segment rows yet: the first data refresh has not landed.', secondary: null, icon: 'db' },
};

/** At most two next steps: one primary recovery and one alternative. */
export type EmptyActions = readonly [] | readonly [ReactNode] | readonly [ReactNode, ReactNode];

interface EmptyStateProps {
  cause: EmptyCause;
  /** Overrides the cause's default sentence. */
  title?: string;
  /** Overrides the cause's second line; null removes it. */
  secondary?: string | null;
  actions?: EmptyActions;
}

export function EmptyState({ cause, title, secondary, actions = [] }: EmptyStateProps) {
  const defaults = CAUSES[cause];
  const copy = secondary === undefined ? defaults.secondary : secondary;
  const shown = actions.slice(0, 2).filter((action) => action !== null && action !== undefined && action !== false);
  return (
    <div className="empty" role="status" data-empty-cause={cause}>
      <span className="empty__icon" aria-hidden="true">
        <Icon name={defaults.icon} size={14} />
      </span>
      <div className="empty__body">
        <div className="empty__title">{title ?? defaults.title}</div>
        {copy && <div className="empty__copy">{copy}</div>}
        {shown.length > 0 && (
          <div className="empty__actions">
            {/* Positional keys by design: the actions are a fixed, ordered pair. */}
            {shown.map((action, index) => <Fragment key={index}>{action}</Fragment>)}
          </div>
        )}
      </div>
    </div>
  );
}
