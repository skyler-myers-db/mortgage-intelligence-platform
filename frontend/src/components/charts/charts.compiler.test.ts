import { beforeAll, describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the kit sources under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error Read-only React Compiler coverage report (tools/), a Node
// ESM script consumed by this test only.
import { analyzeFile } from '../../../../tools/react_compiler_coverage.mjs';

/**
 * The chart kit compiles under the React Compiler (w4-charts brief item 2),
 * checked at the compile step with the production configuration.
 *
 * The coverage gate (--check) counts bailouts, not render bodies the compiler
 * never sees: a component that hands its props to a plain render function
 * ("a compiled shell") compiles only the shell, or nothing at all when the
 * shell holds no hook, and the gate still reads clean. So this test compares
 * every top-level function a kit component file declares with what the
 * compiler compiled: only the named pure helpers (no JSX, no hooks) may stay
 * outside it.
 */

interface CompiledFunction { name: string; emitted: boolean; memoSlots: number }
interface CoverageReport {
  emitsMemoCache: boolean;
  compiledFunctions: CompiledFunction[];
  compileErrors: unknown[];
  optOutPragmas: unknown[];
}

const analyze = analyzeFile as (relFile: string, source?: string) => CoverageReport;

const KIT_DIR = 'frontend/src/components/charts/';

/** Each kit component file, its component, and the pure helpers it may declare. */
const KIT_FILES: ReadonlyArray<{ file: string; component: string; helpers: readonly string[] }> = [
  { file: 'CountChart.tsx', component: 'CountChart', helpers: [] },
  { file: 'Histogram.tsx', component: 'Histogram', helpers: ['binIsPast'] },
  { file: 'ChartFrame.tsx', component: 'ChartFrame', helpers: [] },
  { file: 'ChartTooltip.tsx', component: 'ChartTooltip', helpers: [] },
];

const TOP_LEVEL_FUNCTION = /^(?:export\s+)?(?:function\s+(\w+)|const\s+(\w+)\s*=\s*(?:\([^)]*\)|\w+)\s*=>)/gm;

function declaredFunctions(source: string): string[] {
  return [...source.matchAll(TOP_LEVEL_FUNCTION)].map((match) => match[1] ?? match[2]);
}

/** The top-level functions the compiler left out, and the report. */
function outsideCompiler(relFile: string, source: string): { outside: string[]; report: CoverageReport } {
  const report = analyze(relFile, source);
  const compiled = new Set(report.compiledFunctions.filter((fn) => fn.emitted).map((fn) => fn.name));
  return { outside: declaredFunctions(source).filter((name) => !compiled.has(name)), report };
}

const kitSource = (file: string) => readFileSync(new URL(`./${file}`, import.meta.url), 'utf8') as string;

// Loading @babel/core plus babel-plugin-react-compiler cold takes several
// seconds on a loaded machine; the compile itself is well under a second.
const COMPILE_TIMEOUT_MS = 60_000;

describe('the chart kit under the production React Compiler configuration', () => {
  beforeAll(() => {
    analyze(`${KIT_DIR}Warm.tsx`, 'export function Warm() { return null; }');
  }, COMPILE_TIMEOUT_MS);

  it.each(KIT_FILES)('$file: $component compiles with its JSX, and nothing else renders outside the compiler', ({ file, component, helpers }) => {
    const { outside, report } = outsideCompiler(`${KIT_DIR}${file}`, kitSource(file));
    expect(report.optOutPragmas).toEqual([]);
    expect(report.compileErrors).toEqual([]);
    expect(report.emitsMemoCache).toBe(true);
    expect(report.compiledFunctions.find((fn) => fn.name === component)?.memoSlots ?? 0).toBeGreaterThan(0);
    expect(outside).toEqual([...helpers]);
  }, COMPILE_TIMEOUT_MS);

  // Non-vacuity control: the shell shape this test exists to catch.
  it('flags a component that hands its render to a plain function', () => {
    const shell = [
      'export function Probe(props: { label: string }) {',
      '  return probe(props);',
      '}',
      'function probe({ label }: { label: string }) {',
      '  return <b>{label}</b>;',
      '}',
      '',
    ].join('\n');
    expect(outsideCompiler(`${KIT_DIR}Probe.tsx`, shell).outside).toEqual(['Probe', 'probe']);
  }, COMPILE_TIMEOUT_MS);
});
