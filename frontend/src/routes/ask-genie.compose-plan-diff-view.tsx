import type { Ref } from 'react';
import { Chip } from '../components/Primitives';
import { publicAgentResponsesText } from '../lib/agentLabels';
import type { PlanStep } from '../types/growthAgent';
import { planDiffSummary, type PlanDiff, type PlanDiffRow, type PlanDiffStatus } from './ask-genie.compose-plan-diff';

// The chip TEXT carries the meaning; the colour only repeats it.
const STATUS: Record<PlanDiffStatus, { label: string; variant: 'neutral' | 'warning' | 'success' | 'danger' }> = {
  kept: { label: 'Kept', variant: 'neutral' },
  changed: { label: 'Changed', variant: 'warning' },
  added: { label: 'Added', variant: 'success' },
  removed: { label: 'Removed', variant: 'danger' },
};

function rowDetail(row: PlanDiffRow, formatParams: (params: PlanStep['params']) => string): string {
  if (row.status === 'changed' && row.before && row.after) {
    if (row.moved) return `Moved from step ${(row.beforeIndex ?? 0) + 1} to step ${(row.afterIndex ?? 0) + 1}`;
    return `Was: ${formatParams(row.before.params)} · Now: ${formatParams(row.after.params)}`;
  }
  const step = row.after ?? row.before;
  return step ? formatParams(step.params) : '';
}

interface ComposePlanDiffViewProps {
  diff: PlanDiff;
  scopeChanged: boolean;
  formatParams: (params: PlanStep['params']) => string;
  /** The summary line: focus lands here after Compose again. */
  summaryRef?: Ref<HTMLParagraphElement>;
}

/**
 * What a recompose changed against the plan the lender reviewed (audit
 * 2026-09-21 `critic-01`). deviation:growth-agent-plan-diff: /ask-genie has no
 * prototype page; the rows reuse the floating Genie panel's step-list idiom
 * (design_files/Module 0 Prototype.html :1365) with `.chip` status labels.
 */
export function ComposePlanDiffView({ diff, scopeChanged, formatParams, summaryRef }: ComposePlanDiffViewProps) {
  return (
    <section className="growth-agent-run__section growth-agent-diff" aria-label="Changes since the plan you reviewed">
      <div className="eyebrow">Changes since the plan you reviewed</div>
      <p ref={summaryRef} tabIndex={-1} className="growth-agent-diff__summary">
        {planDiffSummary(diff)}
      </p>
      {scopeChanged && (
        <p className="growth-agent__hint">
          The objective or state scope changed since that plan, which can change the steps.
        </p>
      )}
      <ul className="growth-agent-diff__list">
        {diff.rows.map((row) => (
          <li
            key={`${row.status}-${row.beforeIndex ?? ''}-${row.afterIndex ?? ''}`}
            className={`growth-agent-diff__row growth-agent-diff__row--${row.status}`}
          >
            <Chip variant={STATUS[row.status].variant}>{STATUS[row.status].label}</Chip>
            <div>
              <div className="growth-agent-step__title">{publicAgentResponsesText(row.tool)}</div>
              <div className="growth-agent-step__meta">{rowDetail(row, formatParams)}</div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
