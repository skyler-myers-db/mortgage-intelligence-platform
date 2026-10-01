import { describe, expect, it } from 'vitest';
import type { ComposedPlan, PlanStep } from '../types/growthAgent';
import { diffComposedPlans, planDiffSummary, planScopeChanged, stepSignature } from './ask-genie.compose-plan-diff';

function step(step_id: string, tool: string, params: Record<string, unknown> = {}, rationale = ''): PlanStep {
  return { step_id, tool, params, rationale };
}

function plan(...steps: PlanStep[]): ComposedPlan {
  return { objective_summary: 'Plan.', steps, expected_outcome: '', risk_notes: '', requires_approval: false };
}

function shape(before: ComposedPlan, after: ComposedPlan): string[] {
  return diffComposedPlans(before, after).rows.map((row) => `${row.status}:${row.tool}`);
}

// The fixture pair the lane spec uses (frontend/tests/e2e/fixture/data/growthAgentTrust.ts).
const REVIEWED = plan(
  step('step-1', 'fn_build_cohort', { states: [] }, 'Count the broad borrower cohort.'),
  step('step-2', 'fn_segment_counts', { segment_codes: ['itm', 'listed'], segment_mode: 'any' }, 'Keep eligible leads.'),
  step('step-3', 'fn_lead_queue_url', { segment_codes: ['itm'] }, 'Prepare the handoff.'),
);
const RECOMPOSED = plan(
  REVIEWED.steps[0],
  { ...REVIEWED.steps[1], params: { segment_codes: ['itm'], segment_mode: 'any' }, rationale: 'Reworded.' },
  step('step-3', 'fn_offer_compare', {}, 'Compare offer fit.'),
  { ...REVIEWED.steps[2], step_id: 'step-4' },
);

describe('diffComposedPlans', () => {
  it('reads the fixture recompose as kept, changed, added, kept: renumbering is not a change', () => {
    const diff = diffComposedPlans(REVIEWED, RECOMPOSED);
    expect(diff.rows.map((row) => `${row.status}:${row.tool}`)).toEqual([
      'kept:fn_build_cohort',
      'changed:fn_segment_counts',
      'added:fn_offer_compare',
      'kept:fn_lead_queue_url',
    ]);
    expect(diff.counts).toEqual({ kept: 2, changed: 1, added: 1, removed: 0 });
    expect(planDiffSummary(diff)).toBe('2 kept · 1 changed · 1 added');
    const changed = diff.rows[1];
    expect(changed.before?.params).toEqual({ segment_codes: ['itm', 'listed'], segment_mode: 'any' });
    expect(changed.after?.params).toEqual({ segment_codes: ['itm'], segment_mode: 'any' });
    expect(changed.moved).toBe(false);
  });

  it('ignores step ids and rationale in the signature', () => {
    expect(stepSignature(step('a', 'fn_build_cohort', { states: ['IL'] }, 'One.'))).toBe(
      stepSignature(step('z', 'fn_build_cohort', { states: ['IL'] }, 'Two.')),
    );
  });

  it('treats string-array order, duplicates and empty values as equivalent', () => {
    const before = plan(step('s1', 'fn_segment_counts', { segment_codes: ['listed', 'itm', 'itm'], states: [], note: '', min: null }));
    const after = plan(step('s1', 'fn_segment_counts', { segment_codes: ['itm', 'listed'] }));
    expect(shape(before, after)).toEqual(['kept:fn_segment_counts']);
    expect(planDiffSummary(diffComposedPlans(before, after))).toBe('Same steps as the plan you reviewed.');
  });

  it('reads a param-only change as changed, never as removed plus added', () => {
    const before = plan(step('s1', 'fn_build_cohort', { states: ['IL'] }));
    const after = plan(step('s1', 'fn_build_cohort', { states: ['TX'] }));
    expect(shape(before, after)).toEqual(['changed:fn_build_cohort']);
  });

  it('reads a reorder as a move of the same step, not a change of inputs', () => {
    const a = step('s1', 'fn_build_cohort', { states: ['IL'] });
    const b = step('s2', 'fn_segment_counts', { segment_codes: ['itm'] });
    const diff = diffComposedPlans(plan(a, b), plan({ ...b, step_id: 's1' }, { ...a, step_id: 's2' }));
    // One of the two stays in sequence (kept); the other is the same step at a new position.
    expect(diff.rows.map((row) => `${row.status}:${row.tool}:${row.moved}`)).toEqual([
      'kept:fn_segment_counts:false',
      'changed:fn_build_cohort:true',
    ]);
    expect([diff.rows[1].beforeIndex, diff.rows[1].afterIndex]).toEqual([0, 1]);
    expect(planDiffSummary(diff)).toBe('1 kept · 1 changed');
  });

  it('pairs duplicate tools in order and places a removed step after its predecessor', () => {
    const before = plan(
      step('s1', 'fn_build_cohort'),
      step('s2', 'fn_segment_counts', { segment_codes: ['itm'] }),
      step('s3', 'fn_segment_counts', { segment_codes: ['listed'] }),
      step('s4', 'fn_offer_compare'),
      step('s5', 'fn_lead_queue_url'),
    );
    const after = plan(
      step('s1', 'fn_build_cohort'),
      step('s2', 'fn_segment_counts', { segment_codes: ['equity'] }),
      step('s3', 'fn_lead_queue_url'),
    );
    expect(shape(before, after)).toEqual([
      'kept:fn_build_cohort',
      'changed:fn_segment_counts',
      'removed:fn_segment_counts',
      'removed:fn_offer_compare',
      'kept:fn_lead_queue_url',
    ]);
    const diff = diffComposedPlans(before, after);
    expect(diff.rows[1].before?.params).toEqual({ segment_codes: ['itm'] });
    expect(diff.counts).toEqual({ kept: 2, changed: 1, added: 0, removed: 2 });
    expect(planDiffSummary(diff)).toBe('2 kept · 1 changed · 2 removed');
  });

  it('leads with a removed first step', () => {
    expect(shape(plan(step('s1', 'fn_build_cohort'), step('s2', 'fn_offer_compare')), plan(step('s1', 'fn_offer_compare')))).toEqual([
      'removed:fn_build_cohort',
      'kept:fn_offer_compare',
    ]);
  });
});

describe('planScopeChanged', () => {
  it('compares the trimmed objective and the sorted states', () => {
    expect(planScopeChanged({ objective: 'Grow refi.', states: ['TX', 'IL'] }, { objective: ' Grow refi. ', states: ['IL', 'TX'] })).toBe(false);
    expect(planScopeChanged({ objective: 'Grow refi.', states: [] }, { objective: 'Grow HELOC.', states: [] })).toBe(true);
    expect(planScopeChanged({ objective: 'Grow refi.', states: ['IL'] }, { objective: 'Grow refi.', states: [] })).toBe(true);
  });
});
