/**
 * The title= ban (2026-09-21 audit critic-08), proven against the real
 * frontend/eslint.config.js the way formattingBan.test.ts proves the
 * formatting contract:
 *   - TITLE_BAN is on (at error) for ordinary route and component source;
 *   - it flags a DOM title on any lowercase element but an iframe, and the
 *     `title` prop of the five DOM-forwarding components;
 *   - it leaves SVG <title> elements, iframes and other components' `title`
 *     props alone;
 *   - TITLE_BAN_ALLOWLIST is shrink-only (each entry's count is the file's
 *     live hit count, so a migrated file leaves the list in the same change);
 *   - the allowlist override keeps the formatting ban on.
 */
import tsParser from '@typescript-eslint/parser';
import { ESLint, Linter } from 'eslint';
import { beforeAll, describe, expect, it } from 'vitest';
import { importUntyped, type NodeFs } from '../test/importUntyped';

const FRONTEND_URL = new URL('../../', import.meta.url);
/** Absolute frontend/ directory, with a trailing slash. */
const FRONTEND = decodeURIComponent(FRONTEND_URL.pathname);
const CONFIG_URL = new URL('eslint.config.js', FRONTEND_URL);

interface BanEntry {
  selector: string;
  message: string;
}

interface TitleBanConfig {
  TITLE_BAN: BanEntry[];
  TITLE_BAN_ALLOWLIST: Record<string, string>;
  FORMATTING_BAN: BanEntry[];
}

function isTitleBanConfig(value: unknown): value is TitleBanConfig {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    Array.isArray(record.TITLE_BAN) &&
    Array.isArray(record.FORMATTING_BAN) &&
    typeof record.TITLE_BAN_ALLOWLIST === 'object' &&
    record.TITLE_BAN_ALLOWLIST !== null
  );
}

let config: TitleBanConfig;
let eslint: ESLint;
let fs: NodeFs;

// Loading eslint.config.js pulls in the typescript-eslint and react-hooks
// plugins; calculateConfigForFile resolves the whole flat config. Both can
// pass the unit-test defaults on a loaded CI box.
const ESLINT_BUDGET_MS = 60_000;

beforeAll(async () => {
  const loaded: unknown = await import(/* @vite-ignore */ CONFIG_URL.href);
  if (!isTitleBanConfig(loaded)) throw new Error('eslint.config.js no longer exports TITLE_BAN / TITLE_BAN_ALLOWLIST');
  config = loaded;
  eslint = new ESLint({ cwd: FRONTEND });
  fs = await importUntyped<NodeFs>('node:fs');
}, ESLINT_BUDGET_MS);

/** The no-restricted-syntax entry (severity + options) a file resolves to under the real config. */
async function restrictedSyntax(file: string): Promise<{ level: number; selectors: string[] }> {
  const resolved = (await eslint.calculateConfigForFile(`${FRONTEND}${file}`)) as { rules?: Record<string, unknown> };
  const entry = resolved.rules?.['no-restricted-syntax'];
  const [level, ...options] = Array.isArray(entry) ? entry : [entry];
  const severity = level === 'error' || level === 2 ? 2 : level === 'warn' || level === 1 ? 1 : 0;
  const selectors = options.map((option) => (typeof option === 'string' ? option : (option as BanEntry).selector));
  return { level: severity, selectors };
}

const selectorsOf = (bans: BanEntry[]) => bans.map((ban) => ban.selector);

/** Lint `code` with ONLY `bans` on; returns the matched messages. */
function violations(code: string, bans: BanEntry[], filename = 'probe.tsx'): string[] {
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
        rules: { 'no-restricted-syntax': ['error', ...bans] },
      },
    ],
    filename,
  );
  const fatal = messages.find((message) => message.fatal);
  if (fatal) throw new Error(`${filename} did not parse: ${fatal.message}`);
  return messages.filter((message) => message.ruleId === 'no-restricted-syntax').map((message) => message.message);
}

const titleHits = (code: string, filename?: string) => violations(code, config.TITLE_BAN, filename);

describe('title ban wiring', { timeout: ESLINT_BUDGET_MS }, () => {
  it('is an error, beside the formatting ban, for ordinary route and component source', async () => {
    for (const file of ['src/routes/home.tsx', 'src/components/mortgage/KpiCard.tsx', 'src/components/layout/Topbar.tsx']) {
      const { level, selectors } = await restrictedSyntax(file);
      expect(level, file).toBe(2);
      expect(selectors, file).toEqual(expect.arrayContaining(selectorsOf(config.TITLE_BAN)));
      expect(selectors, file).toEqual(expect.arrayContaining(selectorsOf(config.FORMATTING_BAN)));
    }
  });

  it('stays out of test code', async () => {
    const { selectors } = await restrictedSyntax('src/components/ui/Tooltip.test.tsx');
    expect(selectors).not.toEqual(expect.arrayContaining(selectorsOf(config.TITLE_BAN)));
  });

  it('keeps the formatting ban on (never off) for every allowlisted file', async () => {
    for (const file of Object.keys(config.TITLE_BAN_ALLOWLIST)) {
      const { level, selectors } = await restrictedSyntax(file);
      expect(level, file).toBe(2);
      expect(selectors, file).toEqual(selectorsOf(config.FORMATTING_BAN));
      // The resolved options still catch a toFixed in that file.
      const bans = config.FORMATTING_BAN.filter((ban) => selectors.includes(ban.selector));
      expect(violations('const a = `${n.toFixed(1)}%`;', bans, file.split('/').pop()), file).toHaveLength(1);
    }
  });
});

describe('title ban selectors', () => {
  it('flags a DOM title on any lowercase element', () => {
    for (const tag of ['span', 'button', 'time', 'a', 'div', 'dd', 'svg']) {
      expect(titleHits(`const el = <${tag} title="x">y</${tag}>;`), tag).toHaveLength(1);
    }
    expect(titleHits('const el = <span title={label} />;')).toHaveLength(1);
  });

  it('flags the title prop of the five DOM-forwarding components', () => {
    for (const component of ['Chip', 'Button', 'EvidenceChip', 'Link', 'NavLink']) {
      expect(titleHits(`const el = <${component} title="x">y</${component}>;`), component).toHaveLength(1);
    }
  });

  it('allows SVG <title> elements, an iframe and other components’ title props', () => {
    const allowed = [
      'const el = <svg role="img"><title>Entrada</title><path d="M0 0" /></svg>;',
      'const el = <iframe title="Preview" src="/x" />;',
      'const el = <PageShell title="Lead Queue">x</PageShell>;',
      'const el = <SurfaceTitle>Recent activity</SurfaceTitle>;',
      'const el = <WarmingUpBlock title="Warming up" />;',
      'const el = <Tooltip content="Refresh"><button type="button" aria-label="Refresh" /></Tooltip>;',
      'const el = <Chip tooltip="Refreshed 2026-10-14 12:00 UTC">Fresh</Chip>;',
    ];
    for (const code of allowed) expect(titleHits(code), code).toEqual([]);
  });
});

/** "<owner lane> - <n> site(s) - <why>" */
const ENTRY = /^(?<owner>[a-z0-9-]+(?: [a-z]+)?) - (?<count>\d+) sites? - (?<why>.{10,})$/;

describe('TITLE_BAN_ALLOWLIST ratchet', () => {
  it('lists only files that exist, each with an owner, a count and a reason', () => {
    for (const [file, reason] of Object.entries(config.TITLE_BAN_ALLOWLIST)) {
      expect(fs.existsSync(`${FRONTEND}${file}`), `${file} no longer exists: remove its entry`).toBe(true);
      expect(reason, file).toMatch(ENTRY);
    }
  });

  it('shrinks: every entry reproduces exactly its stated number of TITLE_BAN hits', () => {
    for (const [file, reason] of Object.entries(config.TITLE_BAN_ALLOWLIST)) {
      const source = fs.readFileSync(`${FRONTEND}${file}`, 'utf8');
      const hits = titleHits(source, file.split('/').pop()).length;
      expect(hits, `${file} is clean now: delete its TITLE_BAN_ALLOWLIST entry`).toBeGreaterThan(0);
      expect(hits, `${file}: the entry's site count is stale (lower it) or a site was added (migrate it)`).toBe(
        Number(ENTRY.exec(reason)?.groups?.count),
      );
    }
  });
});
