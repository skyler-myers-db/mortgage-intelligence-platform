import { beforeAll, describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the component source under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error Read-only React Compiler coverage report (tools/), a Node
// ESM script consumed by this test only.
import { analyzeFile } from '../../../../tools/react_compiler_coverage.mjs';

/**
 * Audit runtime-03 / stack-09: Topbar's async submit kept a value block in a
 * try/catch, a React Compiler 1.0 bailout, so the shell's topbar shipped
 * unmemoized. The shared borrower search (hooks/useBorrowerSearch) removed
 * the try: the submit calls searchNow through .then/.catch. This runs the
 * production compiler configuration over the real source, at the compile
 * step where the bailout lived. Whether the compiled output SHIPS is a
 * measured initial-JS trade (tools/react_compiler_allowlist.json): any
 * 'use no memo' left in the file is the only thing between it and a memo
 * cache, which the first case proves by compiling the file without it.
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
const REL = 'frontend/src/components/layout/Topbar.tsx';
const source = () => readFileSync(new URL('./Topbar.tsx', import.meta.url), 'utf8') as string;

describe('Topbar under the production React Compiler configuration', () => {
  beforeAll(() => {
    analyze(REL, 'export function Warm() { return null; }');
  }, COMPILE_TIMEOUT_MS);

  it('has no try statement and compiles clean, emitting a memo cache, once any budget pragma is stripped', () => {
    const text = source();
    expect(text).not.toMatch(/\btry\s*\{/);
    expect((text.match(/'use no memo';/g) ?? []).length).toBeLessThanOrEqual(1);
    const report = analyze(REL, text.replace(/\n\s*'use no memo';\n/, '\n'));
    expect(report.compileErrors).toEqual([]);
    expect(report.compileSkips).toEqual([]);
    expect(report.optOutPragmas).toEqual([]);
    expect(report.emitsMemoCache).toBe(true);
    const topbar = report.compiledFunctions.find((fn) => fn.name === 'Topbar');
    expect(topbar?.emitted).toBe(true);
    expect(topbar?.memoSlots).toBeGreaterThan(0);
  }, COMPILE_TIMEOUT_MS);

  // Non-vacuity control: the pre-hook submit (a value block in try/catch)
  // must still bail. If this stops failing, the compiler learned the construct.
  it('control: a value block inside try/catch still bails out', () => {
    const text = source().replace(/\n\s*'use no memo';\n/, '\n');
    const marker = '  const submitBorrowerSearch = () => {';
    expect(text).toContain(marker);
    const withTry = text.replace(marker, [
      '  const legacySubmit = async () => {',
      '    const q = borrowerQuery.trim();',
      '    try {',
      '      const rows = await borrowerSearch.searchNow(q);',
      '      const exact = rows.find((row) => row.borrower_id === q);',
      '      const first = exact ?? rows[0];',
      '      if (first) {',
      '        openBorrower(first.borrower_id);',
      '      } else {',
      '        setSearchOpen(true);',
      '      }',
      '    } catch {',
      '      setSearchOpen(false);',
      '    }',
      '  };',
      marker,
    ].join('\n')).replace('submitBorrowerSearch();\n        }}', 'void legacySubmit();\n        }}');
    expect(withTry).toContain('void legacySubmit();');
    const report = analyze(REL, withTry);
    expect(report.compileErrors.length).toBeGreaterThan(0);
  }, COMPILE_TIMEOUT_MS);
});
