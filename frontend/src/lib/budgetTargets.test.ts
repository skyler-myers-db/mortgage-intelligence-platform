import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this test reads the tools under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error The budget report module (tools/) is a Node ESM script consumed by CI and this test only.
import * as tool from '../../../tools/frontend_budget_report.mjs';

/**
 * Audit quality-08: absolute budget targets with dated waivers, the per-chunk
 * JSON report and the step-summary table (tools/frontend_budget_report.mjs,
 * called from tools/check_frontend_budgets.mjs main()). Each problem kind is
 * pinned with its non-vacuity twin; the committed targets file names exactly
 * the gate's budgets.routes keys, every target null until the budget owner
 * sets one.
 */

interface Waiver {
  until: string;
  finding: string;
  owner: string;
  reason: string;
}
interface TargetEntry {
  targetKiB: number | null;
  finding: string;
  waiver: Waiver | null;
}
interface TargetsFile {
  _policy: string[];
  targets: {
    initialJsBr: TargetEntry;
    initialCssBr: TargetEntry;
    totalJsBr: TargetEntry;
    routes: Record<string, TargetEntry>;
  };
}
interface Actuals {
  initialJsBr: number;
  initialCssBr: number;
  totalJsBr: number;
  routes: Record<string, number>;
}
interface Chunk {
  file: string;
  bytes: number;
  gzipBytes: number;
  brBytes: number;
}

const report = tool as {
  evaluateTargets(actuals: Actuals, targets: TargetsFile, today: string): string[];
  chunkReport(chunks: Chunk[], initialFiles: string[], routeClosureFiles: Record<string, string[]>): unknown[];
  summaryTable(report: { actuals: Actuals }, gates: Record<string, number>, targets: TargetsFile, base?: { actuals: Actuals } | null): string;
  parseBudgetFlags(argv: string[], defaults: { distDir: string; buildMetaDir: string }): Record<string, unknown>;
};

const FRONTEND = decodeURIComponent(new URL('../../', import.meta.url).pathname);
const KiB = 1024;
const TODAY = '2026-10-01';
const ROUTE = 'src/routes/home.tsx';

function entry(targetKiB: number | null = null, waiver: Waiver | null = null): TargetEntry {
  return { targetKiB, finding: 'quality-08', waiver };
}

function targets(overrides: Partial<TargetsFile['targets']> = {}): TargetsFile {
  return {
    _policy: [],
    targets: { initialJsBr: entry(), initialCssBr: entry(), totalJsBr: entry(), routes: { [ROUTE]: entry() }, ...overrides },
  };
}

const ACTUALS: Actuals = { initialJsBr: 144 * KiB, initialCssBr: 24 * KiB, totalJsBr: 500 * KiB, routes: { [ROUTE]: 44 * KiB } };
const waiver = (until: string): Waiver => ({ until, finding: 'quality-08', owner: 'w5-budget-owner', reason: 'shell growth' });

describe('frontend budget targets (quality-08)', () => {
  it('passes null targets and actuals within their targets', () => {
    expect(report.evaluateTargets(ACTUALS, targets(), TODAY)).toEqual([]);
    expect(report.evaluateTargets(ACTUALS, targets({ initialJsBr: entry(150) }), TODAY)).toEqual([]);
  });

  it('fails an actual over its target, unless an unexpired waiver covers it', () => {
    expect(report.evaluateTargets(ACTUALS, targets({ initialJsBr: entry(140) }), TODAY)).toEqual([
      'initialJsBr: 144.00 KiB is over its 140 KiB target (quality-08)',
    ]);
    expect(report.evaluateTargets(ACTUALS, targets({ initialJsBr: entry(140, waiver('2026-12-31')) }), TODAY)).toEqual([]);
  });

  it('fails an expired waiver', () => {
    expect(report.evaluateTargets(ACTUALS, targets({ initialJsBr: entry(140, waiver('2026-09-30')) }), TODAY)).toEqual([
      'initialJsBr: waiver expired 2026-09-30 (w5-budget-owner, quality-08)',
    ]);
  });

  it('fails a stale waiver: the dimension is back under its target, or has none', () => {
    expect(report.evaluateTargets(ACTUALS, targets({ initialJsBr: entry(150, waiver('2026-12-31')) }), TODAY)).toEqual([
      'initialJsBr: stale waiver: 144.00 KiB is within 150 target; remove it',
    ]);
    expect(report.evaluateTargets(ACTUALS, targets({ initialJsBr: entry(null, waiver('2026-12-31')) }), TODAY)).toEqual([
      'initialJsBr: stale waiver: 144.00 KiB is within no target; remove it',
    ]);
  });

  it('fails an unknown key and a measured route with no entry', () => {
    const unknown = targets({ routes: { [ROUTE]: entry(), 'src/routes/gone.tsx': entry() } });
    expect(report.evaluateTargets(ACTUALS, unknown, TODAY)).toEqual(['routes.src/routes/gone.tsx: unknown key (no such budget dimension)']);
    const missing = targets({ routes: {} });
    expect(report.evaluateTargets(ACTUALS, missing, TODAY)).toEqual([`routes.${ROUTE}: a measured route with no entry in the targets file`]);
  });

  it('fails a malformed entry or waiver', () => {
    const noFinding = targets({ totalJsBr: { targetKiB: null, waiver: null } as unknown as TargetEntry });
    expect(report.evaluateTargets(ACTUALS, noFinding, TODAY)).toEqual(['totalJsBr: an entry needs targetKiB, finding and waiver']);
    const badWaiver = targets({ totalJsBr: entry(400, { until: '2026-12-31' } as Waiver) });
    expect(report.evaluateTargets(ACTUALS, badWaiver, TODAY)).toEqual(['totalJsBr: a waiver needs until, finding, owner, reason']);
  });

  it('the committed targets file names exactly the gate\'s route keys, every target null', () => {
    const committed = JSON.parse(readFileSync(`${FRONTEND}../tools/frontend_budget_targets.json`, 'utf8') as string) as TargetsFile;
    const source = readFileSync(`${FRONTEND}../tools/check_frontend_budgets.mjs`, 'utf8') as string;
    const block = source.slice(source.indexOf('  routes: {'), source.indexOf('\n  },', source.indexOf('  routes: {')));
    const gateRoutes = [...block.matchAll(/^\s+'(src\/routes\/[^']+)':/gm)].map((match) => match[1]).sort();
    expect(gateRoutes.length, 'non-vacuity: the gate lists route budgets').toBeGreaterThan(10);
    expect(Object.keys(committed.targets.routes).sort()).toEqual(gateRoutes);
    const all = [committed.targets.initialJsBr, committed.targets.initialCssBr, committed.targets.totalJsBr, ...Object.values(committed.targets.routes)];
    expect(all.every((item) => item.targetKiB === null && item.waiver === null && item.finding === 'quality-08')).toBe(true);
  });

  it('builds the per-chunk report with initial flags and route keys', () => {
    const chunks: Chunk[] = [
      { file: 'assets/b.js', bytes: 300, gzipBytes: 200, brBytes: 100 },
      { file: 'assets/a.js', bytes: 30, gzipBytes: 20, brBytes: 10 },
    ];
    expect(report.chunkReport(chunks, ['assets/a.js'], { [ROUTE]: ['assets/b.js'], 'src/routes/x.tsx': ['assets/b.js'] })).toEqual([
      { file: 'assets/a.js', raw: 30, gzip: 20, br: 10, initial: true, routeKeys: [] },
      { file: 'assets/b.js', raw: 300, gzip: 200, br: 100, initial: false, routeKeys: [ROUTE, 'src/routes/x.tsx'] },
    ]);
  });

  it('renders a deterministic summary, with base and delta columns only when a base is given', () => {
    const gates = { initialJsBr: 152 * KiB, initialCssBr: 26 * KiB, totalJsBr: 528 * KiB, [`routes.${ROUTE}`]: 47 * KiB };
    const plain = report.summaryTable({ actuals: ACTUALS }, gates, targets({ initialJsBr: entry(150) }));
    expect(plain).toBe(report.summaryTable({ actuals: ACTUALS }, gates, targets({ initialJsBr: entry(150) })));
    expect(plain.split('\n').slice(2, 5)).toEqual([
      '| dimension | actual | gate | target |',
      '| --- | --- | --- | --- |',
      '| `initialJsBr` | 144.00 | 152.00 | 150.00 |',
    ]);
    const base = { actuals: { ...ACTUALS, initialJsBr: 143 * KiB, routes: {} } };
    const delta = report.summaryTable({ actuals: ACTUALS }, gates, targets(), base).split('\n');
    expect(delta[2]).toBe('| dimension | actual | gate | target | base | delta |');
    expect(delta[4]).toBe('| `initialJsBr` | 144.00 | 152.00 | - | 143.00 | +1.00 |');
    expect(delta.find((line) => line.includes(ROUTE))).toBe(`| \`routes.${ROUTE}\` | 44.00 | 47.00 | - | - | new |`);
  });

  it('parses the checker flags and refuses an unknown one', () => {
    const defaults = { distDir: '/d', buildMetaDir: '/m' };
    expect(report.parseBudgetFlags([], defaults)).toEqual({ json: null, base: null, reportOnly: false, distDir: '/d', buildMetaDir: '/m' });
    expect(report.parseBudgetFlags(['--json', 'o.json', '--base', 'b.json', '--report-only', '--dist', 'x', '--build-meta', 'y'], defaults)).toEqual({
      json: 'o.json', base: 'b.json', reportOnly: true, distDir: 'x', buildMetaDir: 'y',
    });
    expect(() => report.parseBudgetFlags(['--jsn', 'o'], defaults)).toThrow(/unknown flag/);
    expect(() => report.parseBudgetFlags(['--json'], defaults)).toThrow(/needs a value/);
  });
});
