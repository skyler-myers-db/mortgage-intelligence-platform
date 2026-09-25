import { afterAll, beforeAll, describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types exclude Node globals; this test writes probe files under Vitest only.
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
// @ts-expect-error Same: the probe directory lives in the OS temp dir.
import { tmpdir } from 'node:os';
// @ts-expect-error The oxlint ratchet (tools/) is a Node ESM script consumed by CI and this test only.
import * as tool from '../../../tools/oxlint_ratchet.mjs';

/**
 * Audit a11y-05 item 2: the oxlint jsx-a11y ratchet. `npm run lint:a11y`
 * (and CI's own step) runs tools/oxlint_ratchet.mjs --check against
 * frontend/oxlint-baseline.json, keyed by FILE + RULE + COUNT, never line.
 * These tests pin the verdicts (UNLISTED, GROWN, STALE, version, suppressed)
 * on synthetic maps, the fail-closed parse, the governed pure-move path, and
 * then run the REAL pinned oxlint with the committed config over a probe
 * file, the layer where a jsx-a11y defect is caught or missed.
 */

type Files = Record<string, Record<string, number>>;
interface Move { from: string; to: string; rules: Record<string, number>; recorded: string }
interface Baseline {
  oxlintVersion: string;
  config: string;
  scope: string;
  policy: { moves: Move[] } & Record<string, unknown>;
  files: Files;
}
interface Suppression { file: string; line: number; directive: string }
interface Verdict {
  unlisted: Array<{ file: string; rule: string; count: number }>;
  grown: Array<{ file: string; rule: string; count: number; allowed: number }>;
  stale: Array<{ file: string; rule: string; count: number; allowed: number; gone: boolean }>;
  suppressions: Suppression[];
  versionMismatch: { baseline: string; installed: string } | null;
}
interface Diagnostic { code?: string; filename: string; message: string; severity?: string; labels?: Array<{ span: { line: number } }> }
interface Run { diagnostics: Diagnostic[]; fileCount: number; ruleCount: number; measured: Files; lines: Record<string, Array<number | null>> }
interface Config { plugins: string[]; categories: Record<string, string>; rules: Record<string, string>; ignorePatterns: string[] }
interface RatchetOptions { version: string; suppressions?: Suppression[]; moves?: Array<{ from: string; to: string }>; recorded?: string }

const evaluate = tool.evaluateBaseline as (measured: Files, baseline: Baseline, options?: { version: string; suppressions?: Suppression[] }) => Verdict;
const ratchet = tool.ratchetBaseline as (measured: Files, baseline: Baseline, options: RatchetOptions) => Baseline;
const bootstrap = tool.bootstrapBaseline as (measured: Files, version: string) => Baseline;
const serialize = tool.serializeBaseline as (baseline: Baseline) => string;
const validateBaseline = tool.validateBaseline as (baseline: unknown) => string[];
const validateConfig = tool.validateConfig as (config: unknown, installed?: string[]) => string[];
const enabledRules = tool.enabledRules as (config: Config) => string[];
const parseOutput = tool.parseOxlintOutput as (stdout: string, expectedRules: number) => { fileCount: number; ruleCount: number };
const aggregate = tool.aggregate as (diagnostics: Diagnostic[], where: { cwd: string; root: string }) => { measured: Files };
const findSuppressions = tool.findSuppressions as (sources: Array<{ file: string; text: string }>) => Suppression[];
const formatVerdict = tool.formatVerdict as (verdict: Verdict) => string[];
const ruleTotals = tool.ruleTotals as (files: Files) => Record<string, number>;
const runOxlint = tool.runOxlint as (options: { targets: string[]; cwd: string; root: string; config?: string }) => Run;
const installedRules = tool.installedRules as () => string[];
const installedVersion = tool.installedVersion as () => string;
const read = readFileSync as (file: string | URL, encoding: 'utf8') => string;
const write = writeFileSync as (file: string, text: string) => void;

// Built by concatenation so this file never carries a directive the real
// tree's suppression scan would (rightly) flag.
const OX_DISABLE = 'oxlint-' + 'disable';
const ES_DISABLE = 'eslint-' + 'disable';
const ALT = 'jsx-a11y/alt-text';
const ROLE = 'jsx-a11y/no-autofocus';
const A = 'frontend/src/components/A.tsx';
const B = 'frontend/src/components/B.tsx';
const NEW = 'frontend/src/components/New.tsx';

function baseline(files: Files, version = '1.85.0'): Baseline {
  return bootstrap(files, version);
}

describe('evaluateBaseline (file + rule + count)', () => {
  const base = baseline({ [A]: { [ALT]: 2 }, [B]: { [ROLE]: 1 } });

  it('passes the recorded tree', () => {
    expect(evaluate({ [A]: { [ALT]: 2 }, [B]: { [ROLE]: 1 } }, base, { version: '1.85.0' })).toEqual({
      unlisted: [], grown: [], stale: [], suppressions: [], versionMismatch: null,
    });
  });

  it('flags a new file+rule key as UNLISTED, in a new file and in a listed one', () => {
    const verdict = evaluate({ [A]: { [ALT]: 2, [ROLE]: 1 }, [B]: { [ROLE]: 1 }, [NEW]: { [ALT]: 1 } }, base, { version: '1.85.0' });
    expect(verdict.unlisted).toEqual([{ file: A, rule: ROLE, count: 1 }, { file: NEW, rule: ALT, count: 1 }]);
    expect(formatVerdict(verdict).join('\n')).toContain(`UNLISTED ${NEW}: ${ALT} x1`);
  });

  it('flags a count above the baseline as GROWN', () => {
    const verdict = evaluate({ [A]: { [ALT]: 3 }, [B]: { [ROLE]: 1 } }, base, { version: '1.85.0' });
    expect(verdict.grown).toEqual([{ file: A, rule: ALT, count: 3, allowed: 2 }]);
  });

  it('flags a lower count and a file with no hit left as STALE', () => {
    const verdict = evaluate({ [A]: { [ALT]: 1 } }, base, { version: '1.85.0' });
    expect(verdict.stale).toEqual([
      { file: A, rule: ALT, count: 1, allowed: 2, gone: false },
      { file: B, rule: ROLE, count: 0, allowed: 1, gone: true },
    ]);
    expect(verdict.unlisted).toEqual([]);
  });

  it('flags a baseline recorded with another oxlint', () => {
    const verdict = evaluate({ [A]: { [ALT]: 2 }, [B]: { [ROLE]: 1 } }, base, { version: '1.86.0' });
    expect(verdict.versionMismatch).toEqual({ baseline: '1.85.0', installed: '1.86.0' });
    expect(formatVerdict(verdict)[0]).toMatch(/^VERSION .*1\.85\.0.*1\.86\.0/);
  });
});

describe('ratchetBaseline', () => {
  const base = baseline({ [A]: { [ALT]: 2, [ROLE]: 1 }, [B]: { [ROLE]: 1 } });

  it('only lowers, drops files with no hit left, and accepts a new oxlintVersion', () => {
    const next = ratchet({ [A]: { [ALT]: 1 } }, base, { version: '1.86.0', recorded: '2026-09-25' });
    expect(next.files).toEqual({ [A]: { [ALT]: 1 } });
    expect(next.oxlintVersion).toBe('1.86.0');
    expect(evaluate({ [A]: { [ALT]: 1 } }, next, { version: '1.86.0' }).stale).toEqual([]);
  });

  it('refuses while a hit is grown or unlisted, or a suppression exists', () => {
    expect(() => ratchet({ [A]: { [ALT]: 3, [ROLE]: 1 }, [B]: { [ROLE]: 1 } }, base, { version: '1.85.0' })).toThrow(/refusing to ratchet/);
    expect(() => ratchet({ [A]: { [ALT]: 1 }, [NEW]: { [ALT]: 1 } }, base, { version: '1.85.0' })).toThrow(/refusing to ratchet/);
    const suppressed = [{ file: A, line: 3, directive: `// ${OX_DISABLE}-next-line` }];
    expect(() => ratchet({ [A]: { [ALT]: 1 } }, base, { version: '1.85.0', suppressions: suppressed })).toThrow(/suppression/);
  });
});

describe('the governed pure move (--moved-from / --moved-to)', () => {
  const base = baseline({ [A]: { [ALT]: 2, [ROLE]: 1 }, [B]: { [ROLE]: 1 } });

  it('transfers the pairs that went stale in the old file to the new file and records the move', () => {
    const measured = { [A]: { [ALT]: 1 }, [NEW]: { [ALT]: 1, [ROLE]: 1 }, [B]: { [ROLE]: 1 } };
    const next = ratchet(measured, base, { version: '1.85.0', moves: [{ from: A, to: NEW }], recorded: '2026-09-25' });
    expect(next.files).toEqual({ [A]: { [ALT]: 1 }, [B]: { [ROLE]: 1 }, [NEW]: { [ALT]: 1, [ROLE]: 1 } });
    expect(next.policy.moves).toEqual([{ from: A, to: NEW, rules: { [ALT]: 1, [ROLE]: 1 }, recorded: '2026-09-25' }]);
    expect(ruleTotals(next.files)).toEqual(ruleTotals(base.files));
    expect(evaluate(measured, next, { version: '1.85.0' })).toMatchObject({ unlisted: [], grown: [], stale: [] });
  });

  it('fails when the new file carries a moved hit plus a new one', () => {
    const measured = { [A]: { [ALT]: 1 }, [NEW]: { [ALT]: 2, [ROLE]: 1 }, [B]: { [ROLE]: 1 } };
    expect(() => ratchet(measured, base, { version: '1.85.0', moves: [{ from: A, to: NEW }] })).toThrow(/only 1 left/);
  });

  it('fails when the move lands in a file the baseline already lists', () => {
    const measured = { [A]: { [ALT]: 1 }, [B]: { [ROLE]: 1, [ALT]: 1 } };
    expect(() => ratchet(measured, base, { version: '1.85.0', moves: [{ from: A, to: B }] })).toThrow(/already has a baseline entry/);
  });
});

describe('fail-closed parsing', () => {
  const report = (fields: Record<string, unknown>) => JSON.stringify({ diagnostics: [], ...fields });

  it('rejects unparseable output (a config or runtime error) and a missing summary', () => {
    expect(() => parseOutput('Failed to parse oxlint configuration file.', 35)).toThrow(/not JSON/);
    expect(() => parseOutput(report({}), 35)).toThrow(/summary/);
  });

  it('rejects a vacuous run: no file linted, or a rule count other than the config enables', () => {
    expect(() => parseOutput(report({ number_of_files: 0, number_of_rules: 35 }), 35)).toThrow(/no file was linted/);
    expect(() => parseOutput(report({ number_of_files: 9, number_of_rules: 34 }), 35)).toThrow(/34 rules ran/);
    expect(parseOutput(report({ number_of_files: 9, number_of_rules: 35 }), 35)).toMatchObject({ fileCount: 9, ruleCount: 35 });
  });

  it('normalizes both code spellings and rejects a non-jsx-a11y diagnostic as config drift', () => {
    const where = { cwd: '/repo/frontend', root: '/repo' };
    const hit = (code: string) => ({ code, filename: 'src/components/A.tsx', message: 'm' });
    expect(aggregate([hit('jsx-a11y(alt-text)'), hit('eslint-plugin-jsx-a11y(alt-text)')], where).measured).toEqual({ [A]: { [ALT]: 2 } });
    expect(() => aggregate([hit('eslint(no-debugger)')], where)).toThrow(/config drift/);
    expect(() => aggregate([{ filename: 'src/components/A.tsx', message: 'Unexpected token' }], where)).toThrow(/config drift/);
  });
});

describe('suppressions are banned, not ratcheted', () => {
  it('flags oxlint directives and eslint directives that are bare or name a jsx-a11y rule', () => {
    const text = [
      `// ${OX_DISABLE}-next-line ${ALT}`,
      `// ${ES_DISABLE}-next-line`,
      `/* ${ES_DISABLE} */`,
      `<img /> // ${ES_DISABLE}-line ${ALT}`,
      `{/* ${OX_DISABLE}-next-line */}`,
      `// ${ES_DISABLE}-next-line react-hooks/exhaustive-deps -- keyed on the one-shot request only`,
    ].join('\n');
    expect(findSuppressions([{ file: A, text }]).map((s) => s.line)).toEqual([1, 2, 3, 4, 5]);
  });
});

describe('serialization and the committed baseline', () => {
  it('is deterministic: sorted keys, one file per line, trailing newline', () => {
    const one = serialize(baseline({ [B]: { [ROLE]: 1 }, [A]: { [ROLE]: 1, [ALT]: 2 } }));
    const two = serialize(baseline({ [A]: { [ALT]: 2, [ROLE]: 1 }, [B]: { [ROLE]: 1 } }));
    expect(one).toBe(two);
    expect(one.endsWith('}\n')).toBe(true);
    expect(one).toContain(`    "${A}": {"${ALT}":2,"${ROLE}":1},\n    "${B}": {"${ROLE}":1}\n`);
    expect(validateBaseline(JSON.parse(one))).toEqual([]);
  });

  it('the committed baseline is valid, canonical and recorded with the installed oxlint', () => {
    const text = read(new URL('../../oxlint-baseline.json', import.meta.url), 'utf8');
    const committed = JSON.parse(text) as Baseline;
    expect(validateBaseline(committed)).toEqual([]);
    expect(serialize(committed)).toBe(text);
    expect(committed.oxlintVersion).toBe(installedVersion());
  });
});

// A cold oxlint spawn is fast, but a loaded CI box can stall a child process.
const OXLINT_TIMEOUT_MS = 60_000;

describe('a real run of the pinned oxlint with the committed config', () => {
  let dir = '';
  const config = JSON.parse(read(new URL('../../.oxlintrc.json', import.meta.url), 'utf8')) as Config;
  const lint = (source: string): Run => {
    write(`${dir}/probe.tsx`, source);
    return runOxlint({ targets: ['.'], cwd: dir, root: dir });
  };
  const IMG = 'export function Probe() {\n  return <img src="x.png" />;\n}\n';

  beforeAll(() => {
    dir = (mkdtempSync as (prefix: string) => string)(`${(tmpdir as () => string)()}/oxlint-ratchet-`);
  });
  afterAll(() => {
    (rmSync as (target: string, options: { recursive: boolean; force: boolean }) => void)(dir, { recursive: true, force: true });
  });

  it('names every jsx-a11y rule the pinned oxlint lists and turns off only prefer-tag-over-role', () => {
    const installed = installedRules();
    expect(installed.length).toBeGreaterThan(30);
    expect(Object.keys(config.rules).sort()).toEqual(installed);
    expect(validateConfig(config, installed)).toEqual([]);
    expect(Object.keys(config.rules).filter((rule) => config.rules[rule] === 'off')).toEqual(['jsx-a11y/prefer-tag-over-role']);
  }, OXLINT_TIMEOUT_MS);

  it('reports an <img> without alt as exactly one jsx-a11y/alt-text hit', () => {
    const run = lint(IMG);
    expect(run.measured).toEqual({ 'probe.tsx': { [ALT]: 1 } });
    expect(run.fileCount).toBe(1);
    expect(run.ruleCount).toBe(enabledRules(config).length);
    // The pinned JSON shape the aggregator reads.
    expect(run.diagnostics[0]).toMatchObject({ code: 'jsx-a11y(alt-text)', severity: 'error', filename: expect.stringMatching(/probe\.tsx$/) });
    expect(run.diagnostics[0].labels?.[0].span.line).toBe(2);
  }, OXLINT_TIMEOUT_MS);

  it('is line-invariant: moving the violation 5 lines down changes nothing', () => {
    const recorded = baseline({ 'frontend/src/probe.tsx': lint(IMG).measured['probe.tsx'] });
    const moved = lint(`\n\n\n\n\n${IMG}`);
    expect(moved.lines[`probe.tsx|${ALT}`]).toEqual([7]);
    const verdict = evaluate({ 'frontend/src/probe.tsx': moved.measured['probe.tsx'] }, recorded, { version: '1.85.0' });
    expect(verdict).toMatchObject({ unlisted: [], grown: [], stale: [] });
  }, OXLINT_TIMEOUT_MS);

  it('honours an eslint-disable comment, which is why the scan bans it', () => {
    const suppressed = `export function Probe() {\n  // ${ES_DISABLE}-next-line\n  return <img src="x.png" />;\n}\n`;
    expect(lint(suppressed).measured).toEqual({});
    expect(findSuppressions([{ file: 'probe.tsx', text: suppressed }])).toHaveLength(1);
  }, OXLINT_TIMEOUT_MS);

  it('fails closed on a config error', () => {
    const bad = { ...config, rules: { ...config.rules, 'jsx-a11y/not-a-rule': 'error' } };
    write(`${dir}/bad.oxlintrc.json`, JSON.stringify(bad));
    expect(() => runOxlint({ targets: ['.'], cwd: dir, root: dir, config: `${dir}/bad.oxlintrc.json` })).toThrow(/not JSON/);
  }, OXLINT_TIMEOUT_MS);
});
