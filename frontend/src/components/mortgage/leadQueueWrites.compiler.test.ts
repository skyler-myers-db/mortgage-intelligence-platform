import { beforeAll, describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the component source under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error Read-only React Compiler coverage report (tools/), a Node
// ESM script consumed by this test only.
import { analyzeFile } from '../../../../tools/react_compiler_coverage.mjs';

/**
 * Audit runtime-03 / stack-09: the Lead Queue's governed-write hooks and the
 * ApprovalBanner used to ship unmemoized. The hooks carried 'use no memo'
 * (their try/finally latches, a `new Map()` default parameter and a
 * wall-clock read behind a purity disable), and ApprovalBanner's guard
 * tripped "Handle TryStatement without a catch clause". This runs the
 * production compiler configuration over the real sources, so it fails at
 * the compile step where the bailout lived, not at a helper below it.
 */

interface CompiledFunction { name: string; emitted: boolean; memoSlots: number }
interface CompileIssue { reason: string }
interface CoverageReport {
  emitsMemoCache: boolean;
  compiledFunctions: CompiledFunction[];
  compileErrors: CompileIssue[];
  compileSkips: CompileIssue[];
  optOutPragmas: unknown[];
}

const analyze = analyzeFile as (relFile: string, source?: string) => CoverageReport;

const COMPILE_TIMEOUT_MS = 60_000;

const TARGETS = [
  ['ApprovalBanner.tsx', 'ApprovalBanner'],
  ['useLeadApprovalActions.ts', 'useLeadApprovalActions'],
  ['useLeadSalesActions.ts', 'useLeadSalesActions'],
] as const;

const repoPath = (file: string) => `frontend/src/components/mortgage/${file}`;
const source = (file: string) => readFileSync(new URL(`./${file}`, import.meta.url), 'utf8');

describe('Lead Queue write surfaces under the production React Compiler configuration', () => {
  beforeAll(() => {
    analyze(repoPath('ApprovalBanner.tsx'), 'export function Warm() { return null; }');
  }, COMPILE_TIMEOUT_MS);

  it.each(TARGETS)('%s compiles with no bailout, skip or opt-out and emits a memo cache', (file, fnName) => {
    const report = analyze(repoPath(file));

    expect(report.optOutPragmas).toEqual([]);
    expect(report.compileErrors).toEqual([]);
    expect(report.compileSkips).toEqual([]);
    expect(report.emitsMemoCache).toBe(true);
    const compiled = report.compiledFunctions.find((fn) => fn.name === fnName);
    expect(compiled?.emitted).toBe(true);
    expect(compiled?.memoSlots).toBeGreaterThan(0);
  }, COMPILE_TIMEOUT_MS);

  // Non-vacuity control: the pre-rewrite guard (try/finally, no catch) must
  // still bail. If this stops failing, the compiler learned the construct.
  it('control: the pre-rewrite try/finally guard in ApprovalBanner still bails out', () => {
    const text = source('ApprovalBanner.tsx');
    const rewritten = /const guard = \(fn\?: \(\) => void \| Promise<void>\) => \(\): Promise<void> => \{[\s\S]*?\n {2}\};\n/;
    expect(text).toMatch(rewritten);
    const preRewriteGuard = [
      '  const guard = (fn?: () => void | Promise<void>) => async () => {',
      '    if (!fn) return;',
      '    if (gated || inFlightRef.current || busy) return;',
      '    inFlightRef.current = true;',
      '    try {',
      '      await fn();',
      '    } finally {',
      '      inFlightRef.current = false;',
      '    }',
      '  };',
      '',
    ].join('\n');
    const withTryFinally = text.replace(rewritten, preRewriteGuard);
    expect(withTryFinally).toContain('} finally {');

    const report = analyze(repoPath('ApprovalBanner.tsx'), withTryFinally);

    expect(report.emitsMemoCache).toBe(false);
    expect(report.compileErrors.map((error) => error.reason)).toEqual([
      expect.stringContaining('Handle TryStatement without a catch clause'),
    ]);
  }, COMPILE_TIMEOUT_MS);
});
