/**
 * tools/lint_depth_probe.mjs (W5e w5-compiler-lint item 0) is copied out of
 * this branch and run against OTHER worktrees, so it embeds the ban and the
 * flag list instead of importing them. These pins keep the copies honest:
 *   - its TITLE_BAN deep-equals the TITLE_BAN frontend/eslint.config.js exports;
 *   - its flag list equals tools/ts_strict_ratchet_baseline.mjs REQUIRED_FLAGS;
 *   - its typed rules are the typed-lint block's rules;
 *   - its tsc CLI carries the ES2022 target/lib and every flag.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import { importUntyped } from './importUntyped';

interface BanEntry { selector: string; message: string }
interface Probe {
  TITLE_BAN: BanEntry[];
  PROBE_FLAGS: string[];
  TYPED_RULES: string[];
  tscProbeArgs: () => string[];
  isProductionSource: (rel: string) => boolean;
  parseTscLines: (stdout: string) => Array<{ file: string; line: number; code: string; message: string }>;
}
interface EslintConfig { TITLE_BAN: BanEntry[]; TYPED_LINT_RULES: Record<string, string> }
interface Ratchet { REQUIRED_FLAGS: string[] }

let probe: Probe;
let eslintConfig: EslintConfig;
let ratchet: Ratchet;

beforeAll(async () => {
  probe = await importUntyped<Probe>(new URL('../../../tools/lint_depth_probe.mjs', import.meta.url));
  eslintConfig = await importUntyped<EslintConfig>(new URL('../../eslint.config.js', import.meta.url));
  ratchet = await importUntyped<Ratchet>(new URL('../../../tools/ts_strict_ratchet_baseline.mjs', import.meta.url));
}, 60_000);

describe('lint-depth probe copies', () => {
  it('embeds exactly the eslint.config.js TITLE_BAN', () => {
    expect(probe.TITLE_BAN).toEqual(eslintConfig.TITLE_BAN);
  });

  it('embeds exactly the ratchet REQUIRED_FLAGS', () => {
    expect(probe.PROBE_FLAGS).toEqual(ratchet.REQUIRED_FLAGS);
  });

  it('lints with exactly the typed-lint block rules, all at error', () => {
    expect([...probe.TYPED_RULES].sort()).toEqual(Object.keys(eslintConfig.TYPED_LINT_RULES).sort());
    expect(new Set(Object.values(eslintConfig.TYPED_LINT_RULES))).toEqual(new Set(['error']));
  });

  it('runs tsc with the ES2022 target/lib and every flag', () => {
    const args = probe.tscProbeArgs();
    expect(args.slice(0, 7)).toEqual(['--noEmit', '--pretty', 'false', '--target', 'ES2022', '--lib', 'DOM,DOM.Iterable,ES2022']);
    expect(args.slice(7)).toEqual(ratchet.REQUIRED_FLAGS.map((flag) => `--${flag}`));
  });
});

describe('lint-depth probe parsing', () => {
  it('scopes to production source', () => {
    expect(probe.isProductionSource('src/routes/home.tsx')).toBe(true);
    expect(probe.isProductionSource('src/lib/time.ts')).toBe(true);
    expect(probe.isProductionSource('src/routes/home.test.tsx')).toBe(false);
    expect(probe.isProductionSource('src/test/render.tsx')).toBe(false);
    expect(probe.isProductionSource('src/mocks/fixtureData.ts')).toBe(false);
    expect(probe.isProductionSource('src/vite-env.d.ts')).toBe(false);
    expect(probe.isProductionSource('tests/e2e/helpers.ts')).toBe(false);
  });

  it('reads located tsc errors and ignores continuation lines', () => {
    const hits = probe.parseTscLines(
      [
        "src/a.ts(3,5): error TS2532: Object is possibly 'undefined'.",
        "  Type 'undefined' is not assignable to type 'number'.",
        '',
        'src/b.tsx(10,1): error TS4114: This member must have an \'override\' modifier.',
      ].join('\n'),
    );
    expect(hits.map((hit) => [hit.file, hit.line, hit.code])).toEqual([
      ['src/a.ts', 3, 'TS2532'],
      ['src/b.tsx', 10, 'TS4114'],
    ]);
  });
});
