import { beforeAll, describe, expect, it } from 'vitest';
import { importUntyped, type NodeFs } from '../../test/importUntyped';

/**
 * Audit runtime-03 / stack-09 (W5e w5-compiler-lint item 7a): the panel's
 * stage handler held a try/finally, a React Compiler 1.0 bailout (a Todo,
 * facebook/react#34131), so the whole panel shipped unmemoized. The write
 * moved to the module-level stageActivationWrite and the handler is a plain
 * call. This runs the production compiler configuration over the real
 * source, the layer where the bailout lived; the control re-inserts the
 * try/finally into the component and proves the bailout is still real.
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
interface CoverageTool { analyzeFile: (relFile: string, source?: string) => CoverageReport }

const COMPILE_TIMEOUT_MS = 60_000;
const REL = 'frontend/src/components/activation/ActivationLoopPanel.tsx';
let analyze: CoverageTool['analyzeFile'];
let source = '';

/** The component body: from its declaration to the end of the file. */
const componentBody = (text: string) => text.slice(text.indexOf('export function ActivationLoopPanel('));

describe('ActivationLoopPanel under the production React Compiler configuration', () => {
  beforeAll(async () => {
    analyze = (await importUntyped<CoverageTool>(new URL('../../../../tools/react_compiler_coverage.mjs', import.meta.url))).analyzeFile;
    source = (await importUntyped<NodeFs>('node:fs')).readFileSync(decodeURIComponent(new URL('./ActivationLoopPanel.tsx', import.meta.url).pathname), 'utf8');
    analyze(REL, 'export function Warm() { return null; }');
  }, COMPILE_TIMEOUT_MS);

  it('keeps no try in the component and compiles clean, emitting a memo cache', () => {
    expect(componentBody(source)).not.toMatch(/\btry\s*\{/);
    expect(source).not.toMatch(/'use no memo'/);
    const report = analyze(REL, source);
    expect(report.compileErrors).toEqual([]);
    expect(report.compileSkips).toEqual([]);
    expect(report.optOutPragmas).toEqual([]);
    expect(report.emitsMemoCache).toBe(true);
    const panel = report.compiledFunctions.find((fn) => fn.name === 'ActivationLoopPanel');
    expect(panel?.emitted).toBe(true);
    expect(panel?.memoSlots).toBeGreaterThan(0);
  }, COMPILE_TIMEOUT_MS);

  // Non-vacuity control: the pre-move handler (try/finally in the component)
  // must still bail. If this stops failing, the compiler learned the construct.
  it('control: a try/finally handler inside the component still bails out', () => {
    const marker = '  const stage = () => {';
    expect(source).toContain(marker);
    const withTry = source.replace(marker, [
      '  const legacyStage = async () => {',
      '    setStaging(true);',
      '    try {',
      '      await queryClient.invalidateQueries({ queryKey: [\'mip\', \'activation\'] });',
      '    } finally {',
      '      setStaging(false);',
      '    }',
      '  };',
      marker,
    ].join('\n')).replace('onClick={() => void stage()}', 'onClick={() => void legacyStage()}');
    expect(withTry).toContain('void legacyStage()');
    const report = analyze(REL, withTry);
    expect(report.compileErrors.length).toBeGreaterThan(0);
  }, COMPILE_TIMEOUT_MS);
});
