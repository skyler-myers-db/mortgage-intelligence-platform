import { beforeAll, describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the component source under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error Read-only React Compiler coverage report (tools/), a Node
// ESM script consumed by this test only.
import { analyzeFile } from '../../../../tools/react_compiler_coverage.mjs';

/**
 * Audit runtime-04 slice 3(b) / runtime-03: the LeadTable shell and its
 * family compile. They used to carry 'use no memo' (a budget trade while the
 * shell stayed uncompiled), a try/finally latch (useLeadCsvExport) and an
 * exhaustive-deps disable (LeadBulkApproveReview). This runs the production
 * compiler configuration over the real sources, so it fails at the compile
 * step where a bailout would live, not at a helper below it. LeadTableBody
 * stays the virtualizer boundary by design (the control below).
 */

interface CompiledFunction { name: string; emitted: boolean; memoSlots: number }
interface CompileIssue { reason: string }
interface CoverageReport {
  emitsMemoCache: boolean;
  compiledFunctions: CompiledFunction[];
  compileErrors: CompileIssue[];
  compileSkips: CompileIssue[];
  pipelineErrors: unknown[];
  optOutPragmas: unknown[];
}

const analyze = analyzeFile as (relFile: string, source?: string) => CoverageReport;

const COMPILE_TIMEOUT_MS = 60_000;

const TARGETS = [
  ['LeadTable.tsx', 'LeadTable'],
  ['LeadTableFooter.tsx', 'LeadTableFooter'],
  ['AssignmentLifecycleAdvance.tsx', 'AssignmentLifecycleAdvance'],
  ['LeadBulkApproveReview.tsx', 'LeadBulkApproveReview'],
  ['useLeadApproveReview.ts', 'useLeadApproveReview'],
  ['useLeadCsvExport.ts', 'useLeadCsvExport'],
  ['useLeadTableCursor.ts', 'useLeadTableCursor'],
  ['useLeadTableHotkeys.ts', 'useLeadTableHotkeys'],
  ['useLeadTableKeyboardFlow.ts', 'useLeadTableKeyboardFlow'],
] as const;

const repoPath = (file: string) => `frontend/src/components/mortgage/${file}`;
const source = (file: string): string => readFileSync(new URL(`./${file}`, import.meta.url), 'utf8');

describe('the LeadTable family under the production React Compiler configuration', () => {
  beforeAll(() => {
    analyze(repoPath('LeadTable.tsx'), 'export function Warm() { return null; }');
  }, COMPILE_TIMEOUT_MS);

  it.each(TARGETS)('%s compiles with no bailout, skip or opt-out and emits a memo cache', (file, fnName) => {
    expect(source(file)).not.toMatch(/'use no (?:memo|forget)'/);
    const report = analyze(repoPath(file));

    expect(report.optOutPragmas).toEqual([]);
    expect(report.compileErrors).toEqual([]);
    expect(report.compileSkips).toEqual([]);
    expect(report.pipelineErrors).toEqual([]);
    expect(report.emitsMemoCache).toBe(true);
    const compiled = report.compiledFunctions.find((fn) => fn.name === fnName);
    expect(compiled?.emitted).toBe(true);
    expect(compiled?.memoSlots).toBeGreaterThan(0);
  }, COMPILE_TIMEOUT_MS);

  // A compiled shell must read each lazy chunk through useLazyModule's
  // store: a render-time `.current()` read would be memoized once (the
  // 5451de15 defect). Callbacks may still call it.
  it('the shell reads no lazy chunk with a render-time .current() call', () => {
    const text = source('LeadTable.tsx');
    expect(text).not.toMatch(/\.module \?\? \w+_CHUNK\.current\(\)/);
    for (const line of text.split('\n').filter((row) => /_CHUNK\.current\(\)/.test(row))) {
      expect(line, line).toMatch(/=>/);
    }
  });

  // Control: the virtualizer's file reports exactly its pragma and the
  // incompatible library it exists to contain (runtime-04 slice 3).
  it('control: LeadTableBody keeps exactly its pragma and the incompatible library', () => {
    const report = analyze(repoPath('LeadTableBody.tsx'));

    expect(report.optOutPragmas).toHaveLength(1);
    expect(report.compileErrors.map((error) => error.reason)).toEqual([
      expect.stringContaining('incompatible library'),
    ]);
  }, COMPILE_TIMEOUT_MS);

  // Non-vacuity control: the pre-rewrite export latch (try/finally) must
  // still bail. If this stops failing, the compiler learned the construct.
  it('control: a try/finally latch in useLeadCsvExport still bails out', () => {
    const text = source('useLeadCsvExport.ts');
    const latch = /return runLeadCsvExport\(request\)\.then\(settle, \(error: unknown\) => settle\(failedLeadCsvExport\(error\)\)\);/;
    expect(text).toMatch(latch);
    const withTryFinally = text.replace(latch, [
      'return (async () => {',
      '      try {',
      '        settle(await runLeadCsvExport(request));',
      '      } finally {',
      '        inflight.current = false;',
      '      }',
      '    })();',
    ].join('\n'));

    const report = analyze(repoPath('useLeadCsvExport.ts'), withTryFinally);

    expect(report.compileErrors.map((error) => error.reason)).toEqual([
      expect.stringContaining('TryStatement'),
    ]);
  }, COMPILE_TIMEOUT_MS);
});
