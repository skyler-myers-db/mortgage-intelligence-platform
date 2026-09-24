import { beforeAll, describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the committed allowlist under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error The React Compiler coverage gate (tools/) is a Node ESM
// script consumed by CI and this test only.
import { analyzeFile, evaluateAllowlist as evaluateFromTool } from '../../../tools/react_compiler_coverage.mjs';
// @ts-expect-error Pure half of the same gate, a Node ESM module.
import * as gate from '../../../tools/react_compiler_allowlist.mjs';

/**
 * Audit runtime-03: the React Compiler coverage gate. A bailout (CompileError,
 * CompileSkip, PipelineError or an opt-out pragma) ships a function
 * unmemoized with no build or lint signal; `--check` fails CI on a bailout in
 * a file tools/react_compiler_allowlist.json does not list, on a count above
 * its entry, and on a stale entry. These tests pin those three verdicts on
 * synthetic reports, then run the REAL production compiler configuration over
 * a new component to prove the gate reaches the layer where the defect lives.
 */

type Counter = 'compileErrors' | 'compileSkips' | 'pipelineErrors' | 'optOutPragmas';
type Counts = Record<Counter, number>;

interface Report {
  file: string;
  compiledFunctions: Array<{ name: string; line: number | null; memoSlots: number; emitted: boolean }>;
  compileErrors: Array<{ fnLine: number | null; reason: string; line: number | null }>;
  compileSkips: Array<{ fnLine: number | null; reason: string; line: number | null }>;
  pipelineErrors: Array<{ fnLine: number | null; data: string }>;
  optOutPragmas: Array<{ line: number | null; directive: string; scope: string }>;
  emitsMemoCache: boolean;
}

interface Entry extends Counts {
  finding: string;
  owner: string;
  note: string;
  recorded: string;
}

interface Allowlist {
  compilerVersion: string;
  files: Record<string, Entry>;
  [key: string]: unknown;
}

interface Bailout { kind: string; line: number | null; reason: string }

interface Verdict {
  unlisted: Array<{ file: string; counts: Counts; bailouts: Bailout[] }>;
  grown: Array<{ file: string; counters: Counter[]; allowed: Counts; counts: Counts }>;
  stale: Array<{ file: string; reason: 'improved' | 'missing'; counters: Counter[] }>;
}

const analyze = analyzeFile as (relFile: string, source?: string) => Report;
const evaluate = gate.evaluateAllowlist as (reports: Report[], allowlist: Allowlist) => Verdict;
const ratchet = gate.ratchetAllowlist as (reports: Report[], allowlist: Allowlist, version: string) => Allowlist;
const validate = gate.validateAllowlist as (allowlist: unknown) => string[];
const serialize = gate.serializeAllowlist as (allowlist: Allowlist) => string;

const TRY_FINALLY = "(BuildHIR::lowerStatement) Handle TryStatement with a finalizer ('finally') clause";

function report(file: string, overrides: Partial<Report> = {}): Report {
  return {
    file,
    compiledFunctions: [],
    compileErrors: [],
    compileSkips: [],
    pipelineErrors: [],
    optOutPragmas: [],
    emitsMemoCache: true,
    ...overrides,
  };
}

function entry(counts: Partial<Counts>): Entry {
  return {
    compileErrors: 0,
    compileSkips: 0,
    pipelineErrors: 0,
    optOutPragmas: 0,
    ...counts,
    finding: 'runtime-03',
    owner: 'test',
    note: 'synthetic',
    recorded: '2026-09-24',
  };
}

const tryFinallyError = (line: number) => ({ fnLine: line - 2, reason: TRY_FINALLY, line });
const pragma = (line: number) => ({ line, directive: 'use no memo', scope: 'module' });

const LISTED = 'frontend/src/routes/listed.tsx';
const ALLOWLIST: Allowlist = {
  compilerVersion: '1.0.0',
  files: {
    [LISTED]: entry({ compileErrors: 2, optOutPragmas: 1 }),
    'frontend/src/routes/deleted.tsx': entry({ compileErrors: 1 }),
  },
};

describe('evaluateAllowlist (pure)', () => {
  it('is the same function the coverage tool exports', () => {
    expect(evaluateFromTool).toBe(gate.evaluateAllowlist);
  });

  it('reports a bailout in an unlisted file with its line and reason', () => {
    const verdict = evaluate(
      [report(LISTED, { compileErrors: [tryFinallyError(10), tryFinallyError(30)], optOutPragmas: [pragma(1)] }),
        report('frontend/src/routes/deleted.tsx', { compileErrors: [tryFinallyError(5)] }),
        report('frontend/src/routes/new.tsx', { compileErrors: [tryFinallyError(12)], emitsMemoCache: false })],
      ALLOWLIST,
    );
    expect(verdict.unlisted).toEqual([
      {
        file: 'frontend/src/routes/new.tsx',
        counts: { compileErrors: 1, compileSkips: 0, pipelineErrors: 0, optOutPragmas: 0 },
        bailouts: [{ kind: 'compileError', line: 12, reason: TRY_FINALLY }],
      },
    ]);
    expect(verdict.grown).toEqual([]);
    expect(verdict.stale).toEqual([]);
  });

  it('reports a count above its entry as grown, even when another counter improved', () => {
    const verdict = evaluate(
      [report(LISTED, { compileErrors: [tryFinallyError(10), tryFinallyError(20), tryFinallyError(30)] }),
        report('frontend/src/routes/deleted.tsx', { compileErrors: [tryFinallyError(5)] })],
      ALLOWLIST,
    );
    expect(verdict.grown.map((item) => [item.file, item.counters])).toEqual([[LISTED, ['compileErrors']]]);
    expect(verdict.stale).toEqual([]);
  });

  it('reports an improved entry and an entry whose file is gone as stale', () => {
    const verdict = evaluate(
      [report(LISTED, { compileErrors: [tryFinallyError(10)], optOutPragmas: [pragma(1)] })],
      ALLOWLIST,
    );
    expect(verdict.unlisted).toEqual([]);
    expect(verdict.grown).toEqual([]);
    expect(verdict.stale.map((item) => [item.file, item.reason, item.counters])).toEqual([
      [LISTED, 'improved', ['compileErrors']],
      ['frontend/src/routes/deleted.tsx', 'missing', []],
    ]);
  });

  it('treats a file that emits no memo cache because it needs no slots as clean', () => {
    const zeroSlot = report('frontend/src/lib/zeroSlot.ts', {
      compiledFunctions: [{ name: 'useZeroSlot', line: 3, memoSlots: 0, emitted: true }],
      emitsMemoCache: false,
    });
    const verdict = evaluate([zeroSlot], { compilerVersion: '1.0.0', files: {} });
    expect(verdict).toEqual({ unlisted: [], grown: [], stale: [] });
  });

  it('ratchets counts down only, drops clean or missing files, and refuses while anything is unlisted', () => {
    const lowered = ratchet(
      [report(LISTED, { compileErrors: [tryFinallyError(10)], optOutPragmas: [pragma(1)] })],
      ALLOWLIST,
      '1.0.1',
    );
    expect(lowered.compilerVersion).toBe('1.0.1');
    expect(Object.keys(lowered.files)).toEqual([LISTED]);
    expect(lowered.files[LISTED]).toEqual({ ...ALLOWLIST.files[LISTED], compileErrors: 1 });

    expect(() => ratchet([report('frontend/src/routes/new.tsx', { optOutPragmas: [pragma(1)] })], ALLOWLIST, '1.0.0'))
      .toThrow(/refusing to ratchet/);
  });

  it('rejects a malformed allowlist instead of reading it as "nothing allowed"', () => {
    expect(validate({ compilerVersion: '1.0.0', files: { [LISTED]: { ...entry({ compileErrors: 1 }), finding: '' } } }))
      .toEqual([`${LISTED}: finding is required`]);
    expect(validate({ compilerVersion: '1.0.0', files: { [LISTED]: entry({}) } }))
      .toEqual([`${LISTED}: an entry that allows nothing must be removed`]);
    expect(validate({ compilerVersion: '1.0.0', files: { [LISTED]: entry({ compileErrors: -1 }) } }))
      .toContain(`${LISTED}: compileErrors must be a non-negative integer`);
  });
});

function committedText(): string {
  return readFileSync(new URL('../../../tools/react_compiler_allowlist.json', import.meta.url), 'utf8') as string;
}

describe('the committed allowlist', () => {
  it('is valid, annotated and in its stable one-entry-per-line form', () => {
    const text = committedText();
    const committed = JSON.parse(text) as Allowlist;
    expect(validate(committed)).toEqual([]);
    expect(serialize(committed)).toBe(text);
    const entryLines = text.split('\n').filter((line) => line.startsWith('    "frontend/src/'));
    expect(entryLines).toHaveLength(Object.keys(committed.files).length);
  });
});

// Loading @babel/core plus babel-plugin-react-compiler cold takes several
// seconds on a loaded machine; the compile itself is well under a second.
const COMPILE_TIMEOUT_MS = 60_000;

describe('the gate over a real compile', () => {
  beforeAll(() => {
    analyze('frontend/src/Warm.tsx', 'export function Warm() { return null; }');
  }, COMPILE_TIMEOUT_MS);

  it('flags a new component with try/finally at an unlisted path', () => {
    const file = 'frontend/src/components/GateProbe.tsx';
    const source = [
      "import { useState } from 'react';",
      'export function GateProbe({ save }: { save: () => Promise<void> }) {',
      '  const [busy, setBusy] = useState(false);',
      '  async function onClick() {',
      '    setBusy(true);',
      '    try {',
      '      await save();',
      '    } catch {',
      '      setBusy(false);',
      '    } finally {',
      '      setBusy(false);',
      '    }',
      '  }',
      '  return <button type="button" disabled={busy} onClick={onClick}>Save</button>;',
      '}',
    ].join('\n');

    const probe = analyze(file, source);
    const verdict = evaluate([probe], JSON.parse(committedText()) as Allowlist);

    expect(probe.emitsMemoCache).toBe(false);
    expect(verdict.unlisted).toHaveLength(1);
    expect(verdict.unlisted[0].file).toBe(file);
    expect(verdict.unlisted[0].bailouts.map((bailout) => bailout.reason)).toEqual([TRY_FINALLY]);
  }, COMPILE_TIMEOUT_MS);
});
