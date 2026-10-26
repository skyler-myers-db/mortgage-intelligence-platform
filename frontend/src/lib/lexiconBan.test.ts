/**
 * The lexicon lint ban (2026-09-21 audit critic-11, D-platform-process-g step
 * 2), proven against the real frontend/eslint.config.js:
 *   - LEXICON_BAN and FORMATTING_BAN both apply to ordinary source;
 *   - the formatting homes keep LEXICON_BAN, lexicon.ts and the pending files
 *     keep FORMATTING_BAN (flat-config options replace each other, so a block
 *     that forgets to restate a ban silently turns it off);
 *   - the selectors bite on literals, template text and JSX text, and leave
 *     the canonical forms and the stable identifiers alone;
 *   - LEXICON_PENDING is shrink-only: each entry must still reproduce a hit.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads eslint.config.js and source files under Vitest only.
import { existsSync, readFileSync } from 'node:fs';
import tsParser from '@typescript-eslint/parser';
import { ESLint, Linter } from 'eslint';
import { beforeAll, describe, expect, it } from 'vitest';

const FRONTEND_URL = new URL('../../', import.meta.url);
const FRONTEND = decodeURIComponent(FRONTEND_URL.pathname);
const CONFIG_URL = new URL('eslint.config.js', FRONTEND_URL);

interface BanEntry {
  selector: string;
  message: string;
}

interface LexiconConfig {
  FORMATTING_BAN: BanEntry[];
  LEXICON_BAN: BanEntry[];
  LEXICON_PENDING: Record<string, string>;
}

function isLexiconConfig(value: unknown): value is LexiconConfig {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    Array.isArray(record.FORMATTING_BAN) &&
    Array.isArray(record.LEXICON_BAN) &&
    typeof record.LEXICON_PENDING === 'object' &&
    record.LEXICON_PENDING !== null
  );
}

let config: LexiconConfig;
let eslint: ESLint;

// Same budget as formattingBan.test.ts: the flat config pulls in plugins.
const ESLINT_BUDGET_MS = 60_000;

beforeAll(async () => {
  const loaded: unknown = await import(/* @vite-ignore */ CONFIG_URL.href);
  if (!isLexiconConfig(loaded)) throw new Error('eslint.config.js no longer exports the lexicon ban');
  config = loaded;
  eslint = new ESLint({ cwd: FRONTEND });
}, ESLINT_BUDGET_MS);

/** The selectors no-restricted-syntax enforces on a file under the real config. */
async function activeSelectors(file: string): Promise<Set<string>> {
  const resolved = (await eslint.calculateConfigForFile(`${FRONTEND}${file}`)) as {
    rules?: Record<string, unknown>;
  };
  const entry = resolved.rules?.['no-restricted-syntax'];
  if (!Array.isArray(entry)) return new Set();
  const [level, ...options] = entry as unknown[];
  if (level !== 'error' && level !== 2) return new Set();
  return new Set(
    options.map((option) => (typeof option === 'string' ? option : (option as BanEntry).selector)),
  );
}

function hasAll(active: Set<string>, ban: BanEntry[]): boolean {
  return ban.every((entry) => active.has(entry.selector));
}

function hasNone(active: Set<string>, ban: BanEntry[]): boolean {
  return ban.every((entry) => !active.has(entry.selector));
}

/** Lint `code` with ONLY the lexicon ban on; returns the matched messages. */
function lexiconHits(code: string, filename = 'probe.tsx'): string[] {
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
        rules: { 'no-restricted-syntax': ['error', ...config.LEXICON_BAN] },
      },
    ],
    filename,
  );
  const fatal = messages.find((message) => message.fatal);
  if (fatal) throw new Error(`${filename} did not parse: ${fatal.message}`);
  return messages.filter((message) => message.ruleId === 'no-restricted-syntax').map((message) => message.message);
}

describe('lexicon ban wiring', { timeout: ESLINT_BUDGET_MS }, () => {
  it('applies both bans to ordinary route source', async () => {
    const active = await activeSelectors('src/routes/home.tsx');
    expect(hasAll(active, config.LEXICON_BAN)).toBe(true);
    expect(hasAll(active, config.FORMATTING_BAN)).toBe(true);
  });

  it('keeps only LEXICON_BAN on a formatting home', async () => {
    const active = await activeSelectors('src/lib/formatters.ts');
    expect(hasAll(active, config.LEXICON_BAN)).toBe(true);
    expect(hasNone(active, config.FORMATTING_BAN)).toBe(true);
  });

  it('keeps only FORMATTING_BAN on lexicon.ts and on every pending file', async () => {
    for (const file of ['src/lib/lexicon.ts', ...Object.keys(config.LEXICON_PENDING)]) {
      const active = await activeSelectors(file);
      expect(hasAll(active, config.FORMATTING_BAN), file).toBe(true);
      expect(hasNone(active, config.LEXICON_BAN), file).toBe(true);
    }
  });

  it('leaves tests and test helpers unbanned', async () => {
    expect((await activeSelectors('src/lib/lexicon.test.ts')).size).toBe(0);
    expect((await activeSelectors('src/test/routeCopyBans.ts')).size).toBe(0);
  });
});

describe('lexicon ban selectors', { timeout: ESLINT_BUDGET_MS }, () => {
  it('bites on every retired variant and shape', () => {
    const cases: Array<[string, string]> = [
      ["const a = 'Refi economics';", 'literal'],
      ["const a = 'Borrowers passing the refinance-economics screen';", 'hyphenated refinance'],
      ["const a = 'Prime Refi Candidates';", 'legacy segment name'],
      ['const a = `x ${y} refi screen`;', 'template text'],
      ['const el = <p>Refi screen</p>;', 'JSX text'],
      ['const el = <KpiCard label="Refi economics screen" />;', 'JSX attribute string'],
      // W5e VRT review: Home's since-last-login label named the In the Money
      // population "refi candidates" and slipped past every scan.
      ["const a = 'refi candidates';", 'refi candidates'],
      ['const a = `Since your last login — ${label} refi candidates`;', 'refi candidates in template text'],
      ["const a = 'Refinance candidate';", 'refinance candidate'],
      ["const a = 'refi-candidates';", 'hyphenated refi candidates'],
    ];
    for (const [code, label] of cases) expect(lexiconHits(code), label).toHaveLength(1);
  });

  it('passes the canonical forms and the stable identifiers', () => {
    const allowed = [
      "const a = 'In the Money';",
      "const a = 'in-the-money';",
      "const a = 'in-the-money borrowers';",
      "const a = 'refi_economics_screen';",
      "const a = 'ITM on Related Property';",
      "const a = 'Refi Propensity';",
      'const el = <p>In the Money rule</p>;',
    ];
    for (const code of allowed) expect(lexiconHits(code), code).toEqual([]);
  });
});

/**
 * The scans that re-implement LEXICON_RE outside eslint: rendered-DOM scans and
 * the backend sweeps of shipped strings and of the Genie space. Each must carry
 * every LEXICON_RE alternative verbatim (a copy may add more, as RETIRED adds
 * `\bprime\b`), so widening the ban widens every scan in the same change.
 */
const LEXICON_RE_COPIES = [
  'src/routes/borrower-360.states.test.tsx',
  'src/routes/glossary.lexicon.test.tsx',
  'tests/e2e/fixture/lexicon-copy.fixture.spec.ts',
  'tests/e2e/fixture/print-glossary-sales-manager.fixture.spec.ts',
  '../tests/unit/test_segment_label_parity.py',
  '../tests/unit/test_provision_genie_space.py',
];

describe('LEXICON_RE copies', { timeout: ESLINT_BUDGET_MS }, () => {
  it('every scan carries the ban\'s alternatives verbatim', () => {
    const selector = config.LEXICON_BAN[0].selector;
    const body = selector.slice(selector.indexOf('=/') + 2, selector.lastIndexOf('/i]'));
    expect(body).toContain(String.raw`\brefi(?:nance)?[- ]candidates?\b`);
    for (const entry of config.LEXICON_BAN) expect(entry.selector, entry.selector).toContain(`/${body}/i]`);
    for (const file of LEXICON_RE_COPIES) {
      expect(readFileSync(`${FRONTEND}${file}`, 'utf8') as string, file).toContain(body);
    }
  });
});

describe('LEXICON_PENDING ratchet', { timeout: ESLINT_BUDGET_MS }, () => {
  it('lists only files that exist, each with a reason', () => {
    for (const [file, reason] of Object.entries(config.LEXICON_PENDING)) {
      expect(existsSync(`${FRONTEND}${file}`), `${file} no longer exists: remove its entry`).toBe(true);
      expect(reason.trim().length, file).toBeGreaterThan(20);
    }
  });

  it('shrinks: every entry still reproduces a hit', () => {
    for (const file of Object.keys(config.LEXICON_PENDING)) {
      const source: string = readFileSync(`${FRONTEND}${file}`, 'utf8');
      expect(
        lexiconHits(source, file.split('/').pop()).length,
        `${file} is clean now: delete its LEXICON_PENDING entry`,
      ).toBeGreaterThan(0);
    }
  });
});
