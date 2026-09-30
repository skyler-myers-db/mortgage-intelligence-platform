import { describe, expect, it } from 'vitest';
// @ts-expect-error The perf ceilings tool (tools/) is a Node ESM script consumed by the integrator and this test only.
import * as tool from '../../../tools/perf_ceilings.mjs';

/**
 * Audit runtime-09 / quality-08: tools/perf_ceilings.mjs turns three
 * reference-runner calibration artifacts of one sha into ceilings (median of
 * the per-run medians x 1.2, rounded up per metric). It refuses local inputs,
 * mixed shas and fewer than three distinct runs, and never writes a spec.
 */

interface Run {
  sha: string | null;
  runId: string | null;
  runAttempt: string | null;
}

interface CalibrationRecord {
  name: string;
  spec: string;
  medians: Record<string, number>;
  samples: unknown[];
  run: Run;
}

interface CeilingResult {
  name: string;
  spec: string;
  ceilings: Record<string, { runMedians: number[]; median: number; ceiling: number }>;
  runs: string[];
  shas: string[];
}

const perf = tool as {
  HEADROOM: number;
  MIN_RUNS: number;
  median(values: number[]): number;
  roundUp(value: number, step: number): number;
  roundingStep(spec: string, metric: string): number;
  refusals(calibrations: CalibrationRecord[], options?: { allowLocal?: boolean; allowMixedSha?: boolean }): string[];
  computeCeilings(calibrations: CalibrationRecord[]): CeilingResult[];
  renderCeilings(results: CeilingResult[]): string;
};

function record(name: string, spec: string, medians: Record<string, number>, runId: string | null, sha: string | null = 'abc123', runAttempt = '1'): CalibrationRecord {
  return { name, spec, medians, samples: [], run: { sha, runId, runAttempt } };
}

const INTERACTION_RUNS = [
  record('interaction-budget', 'interaction-budget', { j: 41, expand: 88, keystroke: 17 }, '101'),
  record('interaction-budget', 'interaction-budget', { j: 44, expand: 95, keystroke: 0 }, '102'),
  record('interaction-budget', 'interaction-budget', { j: 39, expand: 102, keystroke: 21 }, '103'),
];
const VITALS_RUNS = [
  record('home-lab-vitals', 'perf-budget', { lcpMs: 2164, tbtMs: 453, cls: 0.031 }, '101'),
  record('home-lab-vitals', 'perf-budget', { lcpMs: 2076, tbtMs: 393, cls: 0.05 }, '102'),
  record('home-lab-vitals', 'perf-budget', { lcpMs: 2156, tbtMs: 415, cls: 0.042 }, '103'),
];

describe('perf ceilings from calibration (runtime-09 / quality-08)', () => {
  it('takes the middle value, the upper middle of an even count', () => {
    expect(perf.median([3, 1, 2])).toBe(2);
    expect(perf.median([4, 1, 3, 2])).toBe(3);
    expect(() => perf.median([])).toThrow();
  });

  it('rounds up to 10 ms for interactions, 100 ms for LCP / TBT and 0.01 for CLS', () => {
    expect(perf.roundingStep('interaction-budget', 'j')).toBe(10);
    expect(perf.roundingStep('perf-budget', 'lcpMs')).toBe(100);
    expect(perf.roundingStep('perf-budget', 'cls')).toBe(0.01);
    expect(perf.roundUp(52.8, 10)).toBe(60);
    expect(perf.roundUp(50, 10)).toBe(50);
    expect(perf.roundUp(2596.8, 100)).toBe(2600);
    expect(perf.roundUp(0.0504, 0.01)).toBe(0.06);
    expect(perf.roundUp(0.06, 0.01)).toBe(0.06);
  });

  it('computes the 3-run golden: median of the per-run medians x 1.2, rounded up', () => {
    expect(perf.refusals([...INTERACTION_RUNS, ...VITALS_RUNS])).toEqual([]);
    const [home, interaction] = perf.computeCeilings([...INTERACTION_RUNS, ...VITALS_RUNS]);
    expect(interaction.ceilings).toEqual({
      expand: { runMedians: [88, 95, 102], median: 95, ceiling: 120 },
      j: { runMedians: [41, 44, 39], median: 41, ceiling: 50 },
      keystroke: { runMedians: [17, 0, 21], median: 17, ceiling: 30 },
    });
    expect(home.ceilings.lcpMs.ceiling).toBe(2600);
    expect(home.ceilings.tbtMs.ceiling).toBe(500);
    expect(home.ceilings.cls.ceiling).toBe(0.06);
    expect(interaction.runs).toEqual(['101/1', '102/1', '103/1']);
    const text = perf.renderCeilings([interaction]);
    expect(text).toContain('"interaction-budget": { expand: 120, j: 50, keystroke: 30 },');
    expect(text).toContain('// interaction-budget (interaction-budget): runs 101/1, 102/1, 103/1; sha abc123');
  });

  it('refuses fewer than three distinct runs, and the same run twice', () => {
    expect(perf.refusals(INTERACTION_RUNS.slice(0, 2))).toEqual([
      'interaction-budget: 2 run(s); 3 distinct reference-runner runs are required',
    ]);
    const twice = [...INTERACTION_RUNS.slice(0, 2), INTERACTION_RUNS[0]];
    expect(perf.refusals(twice)).toEqual([
      'interaction-budget: the same run (runId/attempt) is given twice',
      'interaction-budget: 2 run(s); 3 distinct reference-runner runs are required',
    ]);
    // A re-run attempt of the same run id is a distinct run.
    const attempts = [...INTERACTION_RUNS.slice(0, 2), { ...INTERACTION_RUNS[0], run: { ...INTERACTION_RUNS[0].run, runAttempt: '2' } }];
    expect(perf.refusals(attempts)).toEqual([]);
  });

  it('refuses a local input unless --allow-local, and mixed shas unless --allow-mixed-sha', () => {
    const local = [record('interaction-budget', 'interaction-budget', { j: 40 }, null, null), ...INTERACTION_RUNS.slice(1)];
    expect(perf.refusals(local).some((problem) => problem.includes('a local input (no runId)'))).toBe(true);
    const locals = [0, 1, 2].map(() => record('interaction-budget', 'interaction-budget', { j: 40 }, null, null));
    expect(perf.refusals(locals, { allowLocal: true })).toEqual([]);

    const mixed = [...INTERACTION_RUNS.slice(0, 2), record('interaction-budget', 'interaction-budget', { j: 40 }, '104', 'def456')];
    expect(perf.refusals(mixed)).toEqual(['interaction-budget: runs of 2 different shas; pass --allow-mixed-sha to accept']);
    expect(perf.refusals(mixed, { allowMixedSha: true })).toEqual([]);
  });

  it('refuses a record that is not a calibration', () => {
    const broken = { ...INTERACTION_RUNS[0], medians: { j: Number.NaN } };
    expect(perf.refusals([broken, ...INTERACTION_RUNS.slice(1)])[0]).toMatch(/not a calibration record/);
  });
});
