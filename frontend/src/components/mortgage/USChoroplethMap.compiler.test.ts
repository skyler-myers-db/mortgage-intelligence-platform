import { beforeAll, describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the component source under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error Read-only React Compiler coverage report (tools/), a Node
// ESM script consumed by this test only.
import { analyzeFile } from '../../../../tools/react_compiler_coverage.mjs';

/**
 * Audit runtime-03: React Compiler 1.0 bails out of the WHOLE component when
 * it meets a value block (`??`, `?:`, `?.`) inside a try/catch, and the
 * bailout is silent (the build succeeds, the react-hooks lint says nothing).
 * The hero map shipped unmemoized for that reason. This test runs the
 * production compiler configuration over the real source file, so it fails
 * at the layer where the defect lived: the compile step, not a helper.
 */

const MAP_FILE = 'frontend/src/components/mortgage/USChoroplethMap.tsx';

interface CompiledFunction { name: string; emitted: boolean; memoSlots: number }
interface CompileError { reason: string; line: number | null }
interface CoverageReport {
  emitsMemoCache: boolean;
  compiledFunctions: CompiledFunction[];
  compileErrors: CompileError[];
  optOutPragmas: unknown[];
}

const analyze = analyzeFile as (relFile: string, source?: string) => CoverageReport;

const mapSource = () => readFileSync(
  new URL('./USChoroplethMap.tsx', import.meta.url),
  'utf8',
);

// Loading @babel/core plus babel-plugin-react-compiler cold takes several
// seconds on a loaded machine; the compile itself is well under a second.
const COMPILE_TIMEOUT_MS = 60_000;

describe('USChoroplethMap under the production React Compiler configuration', () => {
  beforeAll(() => {
    analyze(MAP_FILE, 'export function Warm() { return null; }');
  }, COMPILE_TIMEOUT_MS);

  it('compiles the hero map component and emits a memo cache', () => {
    const report = analyze(MAP_FILE);

    expect(report.optOutPragmas).toEqual([]);
    expect(report.compileErrors).toEqual([]);
    expect(report.emitsMemoCache).toBe(true);
    const component = report.compiledFunctions.find((fn) => fn.name === 'USChoroplethMap');
    expect(component).toBeDefined();
    expect(component?.emitted).toBe(true);
    expect(component?.memoSlots).toBeGreaterThan(0);
  }, COMPILE_TIMEOUT_MS);

  // Non-vacuity control: re-inline the hoisted expression inside the `try`
  // and the compiler must bail again with the documented Todo. If this
  // control ever stops failing, the compiler learned the construct and the
  // hoist can be reverted.
  it('control: the un-hoisted value block inside try/catch still bails out', () => {
    const source = mapSource();
    const hoisted = 'const segmentCodes = segmentFilter ?? [];';
    expect(source).toContain(hoisted);
    expect(source).toContain('segmentCodes,');
    const unhoisted = source
      .replace(hoisted, '')
      .replace('segmentCodes,', 'segmentCodes: segmentFilter ?? [],');

    const report = analyze(MAP_FILE, unhoisted);

    expect(report.emitsMemoCache).toBe(false);
    expect(report.compileErrors.map((error) => error.reason)).toEqual([
      'Support value blocks (conditional, logical, optional chaining, etc) within a try/catch statement',
    ]);
  }, COMPILE_TIMEOUT_MS);
});
