/**
 * A running composed plan's per-step progress (audit 2026-09-21 `genie-09`
 * part 4), mapped onto the card's Plan steps. The server reports step k of N
 * and a phase for the card's OWN execute request; nothing here guesses.
 *
 * - running at k: steps before k are Done, k is Running, later steps Waiting;
 * - gate at k: the same, with k at the Approval gate (the run stops there);
 * - recording after k executed steps: those are Done, the rest Waiting, and
 *   the card says it is recording the audit trail;
 * - not_started (or no answer yet): no mapping, the card stays indeterminate.
 */
import type { GrowthAgentPlanProgressResponse } from '../types/growthAgent';

export type PlanStepProgress = 'completed' | 'running' | 'gate' | 'waiting';

function reported(progress: GrowthAgentPlanProgressResponse | null): progress is GrowthAgentPlanProgressResponse & {
  step_index: number;
  step_total: number;
} {
  return progress !== null && progress.state !== 'not_started' && progress.step_index !== null && progress.step_total !== null;
}

/** The progress of the plan step at 1-based `position`, or null while nothing is reported. */
export function planStepProgress(
  progress: GrowthAgentPlanProgressResponse | null,
  position: number,
): PlanStepProgress | null {
  if (!reported(progress)) return null;
  const current = progress.step_index;
  if (progress.state === 'recording') return position <= current ? 'completed' : 'waiting';
  if (position < current) return 'completed';
  if (position > current) return 'waiting';
  return progress.state === 'gate' ? 'gate' : 'running';
}

/**
 * The polite status line's text. It is a function of (k, phase) only, so a
 * poll that returns the same step re-renders the same text and nothing is
 * re-announced.
 */
export function progressAnnouncement(progress: GrowthAgentPlanProgressResponse | null): string | null {
  if (!reported(progress)) return null;
  if (progress.state === 'recording') return 'Recording the audit trail…';
  const step = `Step ${progress.step_index} of ${progress.step_total}: ${progress.step_tool ?? 'reviewed tool'}`;
  return progress.state === 'gate' ? `${step}, approval gate` : step;
}
