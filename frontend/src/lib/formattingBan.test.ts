/**
 * The formatting lint ban (2026-09-21 audit, responsive-04 / responsive-07),
 * proven against the real frontend/eslint.config.js:
 *   - the ban is ON for ordinary source and catches every banned shape;
 *   - lib/formatters.ts, lib/time.ts and lib/fixedPrecision.ts are its homes;
 *   - FORMATTING_ALLOWLIST is shrink-only: each entry must still reproduce a
 *     violation, so a migrated file has to leave the list in the same change
 *     (the same ratchet as the axe KNOWN_VIOLATIONS list).
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads eslint.config.js and source files under Vitest only.
import { existsSync, readFileSync } from 'node:fs';
import tsParser from '@typescript-eslint/parser';
import { ESLint, Linter } from 'eslint';
import { beforeAll, describe, expect, it } from 'vitest';

const FRONTEND_URL = new URL('../../', import.meta.url);
/** Absolute frontend/ directory, with a trailing slash. */
const FRONTEND = decodeURIComponent(FRONTEND_URL.pathname);
const CONFIG_URL = new URL('eslint.config.js', FRONTEND_URL);

interface BanEntry {
  selector: string;
  message: string;
}

interface FormattingConfig {
  FORMATTING_BAN: BanEntry[];
  FORMATTING_ALLOWLIST: Record<string, string>;
  WAVE_1C_LANE_OWNED: string[];
}

function isFormattingConfig(value: unknown): value is FormattingConfig {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    Array.isArray(record.FORMATTING_BAN) &&
    typeof record.FORMATTING_ALLOWLIST === 'object' &&
    record.FORMATTING_ALLOWLIST !== null &&
    Array.isArray(record.WAVE_1C_LANE_OWNED)
  );
}

let config: FormattingConfig;
let eslint: ESLint;

// Loading eslint.config.js pulls in the typescript-eslint and react-hooks
// plugins, and every calculateConfigForFile resolves the whole flat config:
// on a loaded CI box either one alone can pass the 10 s hook / 5 s test
// defaults, which are sized for unit code, not for a linter.
const ESLINT_BUDGET_MS = 60_000;

beforeAll(async () => {
  const loaded: unknown = await import(/* @vite-ignore */ CONFIG_URL.href);
  if (!isFormattingConfig(loaded)) throw new Error('eslint.config.js no longer exports the formatting ban');
  config = loaded;
  eslint = new ESLint({ cwd: FRONTEND });
}, ESLINT_BUDGET_MS);

/** The effective severity of no-restricted-syntax for a file under the real config. */
async function banSeverity(file: string): Promise<number> {
  const resolved = (await eslint.calculateConfigForFile(`${FRONTEND}${file}`)) as {
    rules?: Record<string, unknown>;
  };
  const entry = resolved.rules?.['no-restricted-syntax'];
  const level = Array.isArray(entry) ? entry[0] : entry;
  if (level === 'error' || level === 2) return 2;
  if (level === 'warn' || level === 1) return 1;
  return 0;
}

/** Lint `code` with ONLY the formatting ban on; returns the matched messages. */
function banViolations(code: string, filename = 'probe.tsx'): string[] {
  const linter = new Linter({ configType: 'flat' });
  const messages = linter.verify(
    code,
    [
      {
        files: ['**/*.{ts,tsx}'],
        languageOptions: {
          parser: tsParser,
          parserOptions: { ecmaVersion: 'latest', sourceType: 'module', ecmaFeatures: { jsx: true } },
        },
        rules: { 'no-restricted-syntax': ['error', ...config.FORMATTING_BAN] },
      },
    ],
    filename,
  );
  const fatal = messages.find((message) => message.fatal);
  if (fatal) throw new Error(`${filename} did not parse: ${fatal.message}`);
  return messages.filter((message) => message.ruleId === 'no-restricted-syntax').map((message) => message.message);
}

describe('formatting ban wiring', { timeout: ESLINT_BUDGET_MS }, () => {
  it('is an error for ordinary route and component source', async () => {
    expect(await banSeverity('src/routes/home.tsx')).toBe(2);
    expect(await banSeverity('src/routes/analytics.sections.tsx')).toBe(2);
    expect(await banSeverity('src/components/mortgage/KpiCard.tsx')).toBe(2);
    // Migrated by the formatters lane, so the feedback-guard glob must not re-open them.
    expect(await banSeverity('src/routes/portfolio-builder.logic.ts')).toBe(2);
    expect(await banSeverity('src/routes/portfolio-builder.components.tsx')).toBe(2);
  });

  it('exempts only the formatting homes and test code', async () => {
    expect(await banSeverity('src/lib/formatters.ts')).toBe(0);
    expect(await banSeverity('src/lib/time.ts')).toBe(0);
    expect(await banSeverity('src/lib/fixedPrecision.ts')).toBe(0);
    expect(await banSeverity('src/lib/formatters.test.ts')).toBe(0);
  });

  it('turns the ban off for every allowlisted file', async () => {
    for (const file of Object.keys(config.FORMATTING_ALLOWLIST)) {
      expect(await banSeverity(file), file).toBe(0);
    }
  });
});

describe('formatting ban selectors', { timeout: ESLINT_BUDGET_MS }, () => {
  it('catches every banned shape', () => {
    const cases: Array<[string, string]> = [
      ['const a = n.toLocaleString();', 'bare toLocaleString'],
      ['const a = row?.count.toLocaleString();', 'optional-chain toLocaleString'],
      ['const a = n.toLocaleString(undefined, { maximumFractionDigits: 1 });', 'browser-locale toLocaleString'],
      ['const a = `${n.toFixed(1)}%`;', 'toFixed'],
      ['const a = d.toLocaleDateString(undefined, { month: "short" });', 'toLocaleDateString'],
      ['const a = d.toLocaleTimeString();', 'toLocaleTimeString'],
      ["const f = new Intl.NumberFormat('en-US');", 'new Intl.NumberFormat'],
      ["const f = new Intl.DateTimeFormat('en-US', { month: 'short' });", 'new Intl.DateTimeFormat'],
      ["const f = new Intl.RelativeTimeFormat('en-US');", 'new Intl.RelativeTimeFormat'],
      ["const f = Intl.NumberFormat('en-US');", 'Intl.NumberFormat without new'],
      ['const el = <span>{count.toLocaleString()}</span>;', 'JSX child'],
    ];
    for (const [code, label] of cases) expect(banViolations(code), label).toHaveLength(1);
  });

  it('leaves the lib calls and an explicitly pinned locale alone', () => {
    const allowed = [
      'const a = formatCount(n);',
      'const a = formatUsdCompact(n);',
      "const a = n.toLocaleString('en-US');",
      'const a = new Date(x).toISOString();',
      'const a = Intl.getCanonicalLocales("en-US");',
    ];
    for (const code of allowed) expect(banViolations(code), code).toEqual([]);
  });
});

describe('FORMATTING_ALLOWLIST ratchet', { timeout: ESLINT_BUDGET_MS }, () => {
  it('lists only files that exist', () => {
    for (const file of Object.keys(config.FORMATTING_ALLOWLIST)) {
      expect(existsSync(`${FRONTEND}${file}`), `${file} no longer exists: remove its entry`).toBe(true);
    }
  });

  it('shrinks: every entry still reproduces a violation', () => {
    for (const file of Object.keys(config.FORMATTING_ALLOWLIST)) {
      const source: string = readFileSync(`${FRONTEND}${file}`, 'utf8');
      expect(
        banViolations(source, file.split('/').pop()).length,
        `${file} is clean now: delete its FORMATTING_ALLOWLIST entry`,
      ).toBeGreaterThan(0);
    }
  });

  it('gives every entry a reason', () => {
    for (const [file, reason] of Object.entries(config.FORMATTING_ALLOWLIST)) {
      expect(reason.trim().length, file).toBeGreaterThan(20);
    }
  });
});
