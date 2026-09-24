/**
 * react-hooks/set-state-in-effect is an ERROR for exactly the files the
 * wave-2 query-layer migration converted (audit stack-09), proven against
 * the real frontend/eslint.config.js the same way formattingBan.test.ts is.
 *
 * The rule stays 'off' repo-wide (its TODO belongs to the wave-4 lint lane),
 * so this pins both sides: the converted files are gated, and an unconverted
 * file is not, which is what catches a premature global flip.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads eslint.config.js under Vitest only.
import { existsSync, globSync } from 'node:fs';
import { ESLint } from 'eslint';
import { beforeAll, describe, expect, it } from 'vitest';

const FRONTEND_URL = new URL('../../', import.meta.url);
/** Absolute frontend/ directory, with a trailing slash. */
const FRONTEND = decodeURIComponent(FRONTEND_URL.pathname);
const CONFIG_URL = new URL('eslint.config.js', FRONTEND_URL);
const RULE = 'react-hooks/set-state-in-effect';

// Loading the config pulls in the typescript-eslint and react-hooks plugins;
// on a loaded CI box that alone can pass the unit-test defaults.
const ESLINT_BUDGET_MS = 60_000;

const CONVERTED = [
  'src/components/mortgage/useLeadApprovalActions.ts',
  'src/components/mortgage/useLeadSalesActions.ts',
  'src/components/mortgage/ApprovalBanner.tsx',
  'src/components/mortgage/GenieHistoryMenu.tsx',
  'src/routes/lead-queue.tsx',
  'src/lib/mutations/outreach.ts',
  'src/lib/mutations/sales.ts',
  'src/lib/mutations/requestIds.ts',
];

const NOT_YET_CONVERTED = [
  'src/components/command/CommandPalette.tsx',
  'src/components/mortgage/LeadTable.tsx',
];

let eslint: ESLint;
let scope: string[];

beforeAll(async () => {
  const loaded: unknown = await import(/* @vite-ignore */ CONFIG_URL.href);
  const exported = (loaded as { SET_STATE_IN_EFFECT_SCOPE?: unknown }).SET_STATE_IN_EFFECT_SCOPE;
  if (!Array.isArray(exported)) throw new Error('eslint.config.js no longer exports SET_STATE_IN_EFFECT_SCOPE');
  scope = exported as string[];
  eslint = new ESLint({ cwd: FRONTEND });
}, ESLINT_BUDGET_MS);

async function severity(file: string): Promise<number> {
  const resolved = (await eslint.calculateConfigForFile(`${FRONTEND}${file}`)) as {
    rules?: Record<string, unknown>;
  };
  const entry = resolved.rules?.[RULE];
  const level = Array.isArray(entry) ? entry[0] : entry;
  if (level === 'error' || level === 2) return 2;
  if (level === 'warn' || level === 1) return 1;
  return 0;
}

describe('set-state-in-effect scope', { timeout: ESLINT_BUDGET_MS }, () => {
  it('is an error for every converted file', async () => {
    for (const file of CONVERTED) {
      expect(existsSync(`${FRONTEND}${file}`), `${file} is missing`).toBe(true);
      expect(await severity(file), file).toBe(2);
    }
  });

  it('stays off for files the migration has not reached (no global flip yet)', async () => {
    for (const file of NOT_YET_CONVERTED) expect(await severity(file), file).toBe(0);
  });

  it('never gates test files', async () => {
    expect(await severity('src/lib/mutations/outreach.test.ts')).toBe(0);
    expect(await severity('src/components/mortgage/ApprovalBanner.test.tsx')).toBe(0);
  });

  it('lists only patterns that match a source file', () => {
    for (const pattern of scope) {
      expect(globSync(pattern, { cwd: FRONTEND }).length, pattern).toBeGreaterThan(0);
    }
  });
});
