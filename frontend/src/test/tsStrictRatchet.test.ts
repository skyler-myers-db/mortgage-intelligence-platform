/**
 * The TypeScript strict ratchet (2026-09-21 audit stack-07 items 3-5;
 * tools/ts_strict_ratchet.mjs, CI's 'TypeScript strict ratchet' step). These
 * tests pin the verdicts (UNLISTED, GROWN, STALE, version, config drift,
 * suppressed) on synthetic maps, the fail-closed tsc parse, the vacuity
 * check, and then validate the COMMITTED configs and baseline, which is the
 * layer a drifted flag or a hand-edited count would break.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import { importUntyped, type NodeFs } from './importUntyped';

type Files = Record<string, Record<string, number>>;
interface Baseline { typescriptVersion: string; config: string; flags: string[]; policy: Record<string, string>; files: Files }
interface Suppression { file: string; line: number; directive: string }
interface Verdict {
  unlisted: Array<{ file: string; code: string; count: number }>;
  grown: Array<{ file: string; code: string; count: number; allowed: number }>;
  stale: Array<{ file: string; code: string; count: number; allowed: number; gone: boolean }>;
  suppressions: Suppression[];
  configProblems: string[];
  versionMismatch: { baseline: string; installed: string } | null;
}
interface TsConfig { extends?: string; compilerOptions?: Record<string, unknown>; include?: string[]; exclude?: string[] }
interface Tool {
  REQUIRED_FLAGS: string[];
  STRICT_EXCLUDE: string[];
  parseTscOutput: (stdout: string) => { hits: Array<{ file: string; line: number; code: string }>; listed: string[] };
  aggregate: (hits: Array<{ file: string; line: number; code: string }>, where: { cwd: string; root: string }) => { measured: Files };
  evaluateBaseline: (measured: Files, baseline: Baseline, options?: { version: string; suppressions?: Suppression[]; configProblems?: string[] }) => Verdict;
  ratchetBaseline: (measured: Files, baseline: Baseline, options: { version: string; suppressions?: Suppression[]; configProblems?: string[] }) => Baseline;
  bootstrapBaseline: (measured: Files, version: string) => Baseline;
  validateBaseline: (baseline: unknown) => string[];
  serializeBaseline: (baseline: Baseline) => string;
  validateStrictConfig: (configs: { buildConfig: TsConfig; strictConfig: TsConfig }) => string[];
  findTsSuppressions: (sources: Array<{ file: string; text: string }>) => Suppression[];
  listedFileProblems: (expected: string[], listed: string[]) => string[];
  formatVerdict: (verdict: Verdict) => string[];
  isProductionSource: (rel: string) => boolean;
  readTsconfig: (file: string) => TsConfig;
  installedVersion: () => string;
  FRONTEND_DIR: string;
  REPO_ROOT: string;
}

let tool: Tool;
let fs: NodeFs;
let buildConfig: TsConfig;
let strictConfig: TsConfig;

beforeAll(async () => {
  tool = await importUntyped<Tool>(new URL('../../../tools/ts_strict_ratchet.mjs', import.meta.url));
  fs = await importUntyped<NodeFs>('node:fs');
  buildConfig = tool.readTsconfig(`${tool.FRONTEND_DIR}/tsconfig.json`);
  strictConfig = tool.readTsconfig(`${tool.FRONTEND_DIR}/tsconfig.strict.json`);
}, 60_000);

const A = 'frontend/src/a.ts';
const B = 'frontend/src/b.tsx';
const base = (files: Files): Baseline => ({ ...tool.bootstrapBaseline(files, '6.0.3') });

describe('ts strict ratchet: parse', () => {
  it('reads located errors and listed files, and ignores continuation lines', () => {
    const { hits, listed } = tool.parseTscOutput(
      [
        '/repo/frontend/node_modules/typescript/lib/lib.es2022.d.ts',
        '/repo/frontend/src/a.ts',
        "src/a.ts(3,5): error TS2532: Object is possibly 'undefined'.",
        "  Type 'undefined' is not assignable to type 'number'.",
        '',
      ].join('\n'),
    );
    expect(hits.map((hit) => [hit.file, hit.line, hit.code])).toEqual([['src/a.ts', 3, 'TS2532']]);
    expect(listed).toEqual(['/repo/frontend/node_modules/typescript/lib/lib.es2022.d.ts', '/repo/frontend/src/a.ts']);
  });

  it('fails closed on an error without a location', () => {
    expect(() => tool.parseTscOutput("error TS5023: Unknown compiler option 'noUncheckedIndexAccess'.")).toThrow(/without a location/);
    expect(() => tool.parseTscOutput("tsconfig.strict.json: error TS6053: File not found.")).toThrow(/without a location/);
  });

  it('aggregates by repo-relative file and code', () => {
    const { measured } = tool.aggregate(
      [
        { file: 'src/a.ts', line: 1, code: 'TS2532' },
        { file: 'src/a.ts', line: 9, code: 'TS2532' },
        { file: 'src/b.tsx', line: 2, code: 'TS18048' },
      ],
      { cwd: '/repo/frontend', root: '/repo' },
    );
    expect(measured).toEqual({ [A]: { TS2532: 2 }, [B]: { TS18048: 1 } });
  });

  it('fails a vacuous or drifted program', () => {
    expect(tool.listedFileProblems([A, B], [A, B, 'frontend/src/data.json'])).toEqual([]);
    expect(tool.listedFileProblems([A, B], [A]).join('\n')).toMatch(/not type-checked: frontend\/src\/b\.tsx/);
    expect(tool.listedFileProblems([A], [A, 'frontend/src/a.test.ts']).join('\n')).toMatch(/not production source/);
    expect(tool.listedFileProblems([], [])).not.toEqual([]);
  });

  it('scopes production source like the strict exclude', () => {
    expect(tool.isProductionSource('src/routes/home.tsx')).toBe(true);
    expect(tool.isProductionSource('src/vite-env.d.ts')).toBe(true);
    expect(tool.isProductionSource('src/routes/home.test.tsx')).toBe(false);
    expect(tool.isProductionSource('src/test/render.tsx')).toBe(false);
    expect(tool.isProductionSource('src/mocks/fixtureData.ts')).toBe(false);
    expect(tool.STRICT_EXCLUDE).toEqual(['src/**/*.test.ts', 'src/**/*.test.tsx', 'src/test/**', 'src/mocks/**']);
  });
});

describe('ts strict ratchet: verdicts', () => {
  const baseline = () => base({ [A]: { TS2532: 2 }, [B]: { TS18048: 1 } });

  it('passes an unchanged tree', () => {
    const verdict = tool.evaluateBaseline({ [A]: { TS2532: 2 }, [B]: { TS18048: 1 } }, baseline(), { version: '6.0.3' });
    expect(tool.formatVerdict(verdict)).toEqual([]);
  });

  it('fails an unlisted code, a grown count, a stale entry and another TypeScript', () => {
    const verdict = tool.evaluateBaseline({ [A]: { TS2532: 3, TS2322: 1 } }, baseline(), { version: '6.1.0' });
    expect(verdict.unlisted).toEqual([{ file: A, code: 'TS2322', count: 1 }]);
    expect(verdict.grown).toEqual([{ file: A, code: 'TS2532', count: 3, allowed: 2 }]);
    expect(verdict.stale).toEqual([{ file: B, code: 'TS18048', count: 0, allowed: 1, gone: true }]);
    expect(verdict.versionMismatch).toEqual({ baseline: '6.0.3', installed: '6.1.0' });
    expect(tool.formatVerdict(verdict)).toHaveLength(4);
  });

  it('fails a lower count as stale', () => {
    const verdict = tool.evaluateBaseline({ [A]: { TS2532: 1 }, [B]: { TS18048: 1 } }, baseline(), { version: '6.0.3' });
    expect(verdict.stale).toEqual([{ file: A, code: 'TS2532', count: 1, allowed: 2, gone: false }]);
  });

  it('reports suppressions and config drift', () => {
    const verdict = tool.evaluateBaseline({ [A]: { TS2532: 2 }, [B]: { TS18048: 1 } }, baseline(), {
      version: '6.0.3',
      suppressions: [{ file: A, line: 4, directive: '@ts-ignore' }],
      configProblems: ['noUncheckedIndexedAccess is on in neither'],
    });
    expect(tool.formatVerdict(verdict)).toHaveLength(2);
  });

  it('ratchets only downwards and drops fixed files', () => {
    const next = tool.ratchetBaseline({ [A]: { TS2532: 1 } }, baseline(), { version: '6.1.0' });
    expect(next.files).toEqual({ [A]: { TS2532: 1 } });
    expect(next.typescriptVersion).toBe('6.1.0');
  });

  it('refuses to ratchet a new or grown hit, a suppression or drift', () => {
    expect(() => tool.ratchetBaseline({ [A]: { TS2532: 3 } }, baseline(), { version: '6.0.3' })).toThrow(/unlisted or above/);
    expect(() => tool.ratchetBaseline({ [A]: { TS7006: 1 } }, baseline(), { version: '6.0.3' })).toThrow(/unlisted or above/);
    expect(() =>
      tool.ratchetBaseline({}, baseline(), { version: '6.0.3', suppressions: [{ file: A, line: 1, directive: '@ts-nocheck' }] }),
    ).toThrow(/suppression/);
    expect(() => tool.ratchetBaseline({}, baseline(), { version: '6.0.3', configProblems: ['x'] })).toThrow(/drifted/);
  });
});

describe('ts strict ratchet: suppressions', () => {
  it('bans @ts-ignore and @ts-nocheck everywhere and @ts-expect-error outside the wire-contract checks', () => {
    const found = tool.findTsSuppressions([
      { file: 'frontend/src/routes/home.tsx', text: 'const a = 1;\n// @ts-ignore\nconst b = c;\n' },
      { file: 'frontend/src/lib/x.ts', text: '/* @ts-nocheck */\n' },
      { file: 'frontend/src/lib/y.ts', text: '// @ts-expect-error a hole\nconst z = 1;\n' },
      { file: 'frontend/src/types/wireContract.leads.check.ts', text: '// @ts-expect-error the proof\n' },
      { file: 'frontend/src/lib/z.ts', text: "const doc = 'mention @ts-ignore in a string';\n" },
    ]);
    expect(found).toEqual([
      { file: 'frontend/src/routes/home.tsx', line: 2, directive: '@ts-ignore' },
      { file: 'frontend/src/lib/x.ts', line: 1, directive: '@ts-nocheck' },
      { file: 'frontend/src/lib/y.ts', line: 1, directive: '@ts-expect-error' },
    ]);
  });
});

describe('ts strict ratchet: the committed configs and baseline', () => {
  it('names the eight audit flags', () => {
    expect(tool.REQUIRED_FLAGS).toEqual([
      'verbatimModuleSyntax',
      'erasableSyntaxOnly',
      'noImplicitOverride',
      'noImplicitReturns',
      'noFallthroughCasesInSwitch',
      'noUnusedLocals',
      'noUnusedParameters',
      'noUncheckedIndexedAccess',
    ]);
  });

  it('has every flag on in the build or the strict config, ES2022, and a production-only strict program', () => {
    expect(tool.validateStrictConfig({ buildConfig, strictConfig })).toEqual([]);
    expect(buildConfig.compilerOptions?.target).toBe('ES2022');
    expect(strictConfig.compilerOptions?.noUncheckedIndexedAccess).toBe(true);
  });

  it('fails config drift', () => {
    const drift = (build: TsConfig, strict: TsConfig) => tool.validateStrictConfig({ buildConfig: build, strictConfig: strict });
    const opts = (config: TsConfig, patch: Record<string, unknown>): TsConfig => ({
      ...config,
      compilerOptions: { ...config.compilerOptions, ...patch },
    });
    const { noUncheckedIndexedAccess: _dropped, ...rest } = strictConfig.compilerOptions ?? {};
    expect(drift(buildConfig, { ...strictConfig, compilerOptions: rest }).join('\n')).toMatch(/noUncheckedIndexedAccess is on in neither/);
    expect(drift(opts(buildConfig, { noUnusedLocals: false }), strictConfig).join('\n')).toMatch(/noUnusedLocals is set to false/);
    expect(drift(opts(buildConfig, { target: 'ES2020' }), strictConfig).join('\n')).toMatch(/target must be ES2022/);
    expect(drift(opts(buildConfig, { lib: ['DOM', 'ES2020'] }), strictConfig).join('\n')).toMatch(/lib must be exactly/);
    expect(drift(buildConfig, { ...strictConfig, exclude: ['src/test/**'] }).join('\n')).toMatch(/production only/);
    expect(drift(buildConfig, { ...strictConfig, extends: './tsconfig.base.json' }).join('\n')).toMatch(/must extend/);
    expect(drift(buildConfig, opts(strictConfig, { skipLibCheck: false })).join('\n')).toMatch(/only the REQUIRED_FLAGS/);
  });

  it('commits a well-formed, deterministic baseline for the installed TypeScript', () => {
    const text = fs.readFileSync(`${tool.REPO_ROOT}/tools/ts_strict_ratchet_baseline.json`, 'utf8');
    const baseline = JSON.parse(text) as Baseline;
    expect(tool.validateBaseline(baseline)).toEqual([]);
    expect(tool.serializeBaseline(baseline)).toBe(text);
    expect(baseline.typescriptVersion).toBe(tool.installedVersion());
    for (const file of Object.keys(baseline.files)) {
      expect(fs.existsSync(`${tool.REPO_ROOT}/${file}`), file).toBe(true);
      expect(tool.isProductionSource(file.replace(/^frontend\//, '')), file).toBe(true);
    }
  });

  it('rejects a malformed baseline', () => {
    const good = base({ [A]: { TS2532: 1 } });
    expect(tool.validateBaseline(good)).toEqual([]);
    expect(tool.validateBaseline({ ...good, flags: ['noUncheckedIndexedAccess'] }).join('\n')).toMatch(/flags must be exactly/);
    expect(tool.validateBaseline({ ...good, files: { [A]: { TS2532: 0 } } }).join('\n')).toMatch(/positive integer/);
    expect(tool.validateBaseline({ ...good, files: { 'tests/a.ts': { TS2532: 1 } } }).join('\n')).toMatch(/repo-relative/);
    expect(tool.validateBaseline({ ...good, files: { [A]: { 'no-unused': 1 } } }).join('\n')).toMatch(/not a TS error code/);
    expect(tool.validateBaseline(null)).not.toEqual([]);
  });
});
