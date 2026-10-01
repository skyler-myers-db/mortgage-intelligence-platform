import type { ComposedPlan, PlanStep } from '../types/growthAgent';

/**
 * Step diff between the composed plan the lender reviewed and the plan a
 * recompose returned (audit 2026-09-21 `critic-01`). Pure: no React, no I/O.
 *
 * A step's signature is its tool plus its canonical params: keys sorted,
 * string arrays de-duplicated and sorted, keys whose value is null, '' or []
 * dropped; `step_id` and `rationale` are ignored, so renumbering or a
 * reworded rationale is not a change.
 *
 * 1. The longest common subsequence of signatures is `kept`.
 * 2. Unmatched steps of the same tool pair up, in order of appearance, as
 *    `changed` (a pair with identical signatures is a step that moved).
 * 3. What is left is `removed` (reviewed plan) or `added` (new plan).
 *
 * Rows come in new-plan order; a removed row follows the row of its nearest
 * surviving predecessor in the reviewed plan (or leads, when it has none).
 */

export type PlanDiffStatus = 'kept' | 'changed' | 'added' | 'removed';

export interface PlanDiffRow {
  status: PlanDiffStatus;
  tool: string;
  /** The reviewed plan's step (kept, changed, removed) and its 0-based position. */
  before: PlanStep | null;
  beforeIndex: number | null;
  /** The new plan's step (kept, changed, added) and its 0-based position. */
  after: PlanStep | null;
  afterIndex: number | null;
  /** A changed pair whose tool and params are identical: only its position moved. */
  moved: boolean;
}

export type PlanDiffCounts = Record<PlanDiffStatus, number>;

export interface PlanDiff {
  rows: PlanDiffRow[];
  counts: PlanDiffCounts;
}

function isEmpty(value: unknown): boolean {
  return value === null || value === undefined || value === '' || (Array.isArray(value) && value.length === 0);
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.every((item) => typeof item === 'string')
      ? Array.from(new Set(value as string[])).sort()
      : value.map(canonical);
  }
  if (value !== null && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys(record)
        .sort()
        .filter((key) => !isEmpty(record[key]))
        .map((key) => [key, canonical(record[key])]),
    );
  }
  return value;
}

/** Tool plus canonical params; never the step id or the rationale. */
export function stepSignature(step: PlanStep): string {
  return JSON.stringify([step.tool, canonical(step.params ?? {})]);
}

/** Index pairs [before, after] of one longest common subsequence. */
function lcsPairs(before: string[], after: string[]): Array<[number, number]> {
  const table = before.map(() => new Array<number>(after.length + 1).fill(0));
  table.push(new Array<number>(after.length + 1).fill(0));
  for (let i = before.length - 1; i >= 0; i -= 1) {
    for (let j = after.length - 1; j >= 0; j -= 1) {
      table[i][j] = before[i] === after[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const pairs: Array<[number, number]> = [];
  let i = 0;
  let j = 0;
  while (i < before.length && j < after.length) {
    if (before[i] === after[j]) {
      pairs.push([i, j]);
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      i += 1;
    } else {
      j += 1;
    }
  }
  return pairs;
}

function row(status: PlanDiffStatus, before: PlanStep[], after: PlanStep[], i: number | null, j: number | null): PlanDiffRow {
  const beforeStep = i === null ? null : before[i];
  const afterStep = j === null ? null : after[j];
  return {
    status,
    tool: (afterStep ?? beforeStep)?.tool ?? '',
    before: beforeStep,
    beforeIndex: i,
    after: afterStep,
    afterIndex: j,
    moved: status === 'changed' && beforeStep !== null && afterStep !== null
      && stepSignature(beforeStep) === stepSignature(afterStep),
  };
}

export function diffComposedPlans(reviewed: ComposedPlan, next: ComposedPlan): PlanDiff {
  const before = reviewed.steps;
  const after = next.steps;
  const partner = new Map<number, number>();
  const afterRows = new Map<number, PlanDiffRow>();
  for (const [i, j] of lcsPairs(before.map(stepSignature), after.map(stepSignature))) {
    partner.set(i, j);
    afterRows.set(j, row('kept', before, after, i, j));
  }
  const pendingByTool = new Map<string, number[]>();
  before.forEach((step, i) => {
    if (!partner.has(i)) pendingByTool.set(step.tool, [...(pendingByTool.get(step.tool) ?? []), i]);
  });
  after.forEach((step, j) => {
    if (afterRows.has(j)) return;
    const i = pendingByTool.get(step.tool)?.shift();
    if (i !== undefined) partner.set(i, j);
    afterRows.set(j, row(i === undefined ? 'added' : 'changed', before, after, i ?? null, j));
  });
  // Removed steps, anchored after their nearest surviving predecessor (-1 leads).
  const removedAfter = new Map<number, PlanDiffRow[]>();
  let anchor = -1;
  before.forEach((_, i) => {
    const j = partner.get(i);
    if (j !== undefined) {
      anchor = j;
      return;
    }
    removedAfter.set(anchor, [...(removedAfter.get(anchor) ?? []), row('removed', before, after, i, null)]);
  });
  const rows = [...(removedAfter.get(-1) ?? [])];
  after.forEach((_, j) => {
    const entry = afterRows.get(j);
    if (entry) rows.push(entry);
    rows.push(...(removedAfter.get(j) ?? []));
  });
  const counts: PlanDiffCounts = { kept: 0, changed: 0, added: 0, removed: 0 };
  for (const entry of rows) counts[entry.status] += 1;
  return { rows, counts };
}

/** The objective and state scope a plan was composed for. */
export interface PlanScope {
  objective: string;
  states: string[];
}

/** True when the trimmed objective or the sorted state scope differs. */
export function planScopeChanged(before: PlanScope, after: PlanScope): boolean {
  const states = (scope: PlanScope) => [...scope.states].sort().join(',');
  return before.objective.trim() !== after.objective.trim() || states(before) !== states(after);
}

const ORDER: PlanDiffStatus[] = ['kept', 'changed', 'added', 'removed'];

/** '2 kept · 1 changed · 1 added', or the same-steps sentence. */
export function planDiffSummary(diff: PlanDiff): string {
  const changes = diff.counts.changed + diff.counts.added + diff.counts.removed;
  if (changes === 0) return 'Same steps as the plan you reviewed.';
  return ORDER.filter((status) => diff.counts[status] > 0)
    .map((status) => `${diff.counts[status]} ${status}`)
    .join(' · ');
}
