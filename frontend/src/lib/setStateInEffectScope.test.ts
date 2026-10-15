/**
 * react-hooks/set-state-in-effect is an ERROR for production sources
 * repo-wide (audit quality-10 step 4 / stack-09 item 5, W5e), proven against
 * the real frontend/eslint.config.js the same way formattingBan.test.ts is.
 *
 * SET_STATE_IN_EFFECT_PENDING is shrink-only: each entry must still
 * reproduce a hit (a fixed file leaves the list in the same change), none
 * may be a file the W5e lead-table lane owns, and test files stay ungated.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads eslint.config.js under Vitest only.
import { existsSync, readFileSync } from 'node:fs';
import { ESLint } from 'eslint';
import { beforeAll, describe, expect, it } from 'vitest';

const FRONTEND_URL = new URL('../../', import.meta.url);
/** Absolute frontend/ directory, with a trailing slash. */
const FRONTEND = decodeURIComponent(FRONTEND_URL.pathname);
const CONFIG_URL = new URL('eslint.config.js', FRONTEND_URL);
const RULE = 'react-hooks/set-state-in-effect';

// Loading the config pulls in the typescript-eslint and react-hooks plugins;
// on a loaded CI box that alone can pass the unit-test defaults, and the
// pending files include the largest Genie modules.
const ESLINT_BUDGET_MS = 120_000;

/** A synchronous setState in an effect body: the rule's textbook hit. */
const PLANTED = [
  "import { useEffect, useState } from 'react';",
  'export function usePlantedMirror(value: number): number {',
  '  const [mirror, setMirror] = useState(value);',
  '  useEffect(() => {',
  '    setMirror(value);',
  '  }, [value]);',
  '  return mirror;',
  '}',
  '',
].join('\n');

/** Production files the W5e lead-table lane edits: never parked on the pending list. */
const LANE_FILES = [
  'src/components/mortgage/LeadTable.tsx',
  'src/components/mortgage/LeadTableBody.tsx',
  'src/components/mortgage/LeadTableRow.tsx',
  'src/components/mortgage/LeadTableFooter.tsx',
  'src/components/mortgage/LeadTableStatusChips.tsx',
  'src/components/mortgage/LeadTableBulkActions.tsx',
  'src/components/mortgage/LeadBulkApproveReview.tsx',
  'src/components/mortgage/AssignmentLifecycleAdvance.tsx',
  'src/components/mortgage/DecisionReceipt.tsx',
  'src/components/mortgage/useLazyModule.ts',
  'src/components/mortgage/useLeadTableCursor.ts',
  'src/components/mortgage/useLeadTableHotkeys.ts',
  'src/components/mortgage/useLeadTableKeyboardFlow.ts',
  'src/components/mortgage/useLeadApproveReview.ts',
  'src/components/mortgage/useLeadApprovalActions.ts',
  'src/components/mortgage/useLeadCsvExport.ts',
  'src/components/mortgage/leadDecisionWrites.ts',
  'src/components/mortgage/leadDecisionToast.ts',
  'src/components/mortgage/salesUndoToasts.ts',
  'src/components/mortgage/salesUndoRun.ts',
  'src/lib/mutations/outreach.ts',
];

/** Production files the old wave-2/4 scope did NOT gate: the flip reaches them. */
const EX_SCOPE = [
  'src/components/command/CommandPalette.tsx',
  'src/components/mortgage/LeadTable.tsx',
  'src/components/mortgage/useLeadTableCursor.ts',
];

let eslint: ESLint;
let strict: ESLint;
let pending: Record<string, string>;

beforeAll(async () => {
  const loaded: unknown = await import(/* @vite-ignore */ CONFIG_URL.href);
  const exported = (loaded as { SET_STATE_IN_EFFECT_PENDING?: unknown }).SET_STATE_IN_EFFECT_PENDING;
  if (exported === null || typeof exported !== 'object') {
    throw new Error('eslint.config.js no longer exports SET_STATE_IN_EFFECT_PENDING');
  }
  pending = exported as Record<string, string>;
  eslint = new ESLint({ cwd: FRONTEND });
  // The same config with the rule forced on everywhere: what each pending
  // file would report once its override goes.
  strict = new ESLint({ cwd: FRONTEND, overrideConfig: { rules: { [RULE]: 'error' } } });
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

async function ruleHits(linter: ESLint, text: string, file: string): Promise<number> {
  const [result] = await linter.lintText(text, { filePath: `${FRONTEND}${file}` });
  return result.messages.filter((message) => message.ruleId === RULE && message.severity === 2).length;
}

describe('set-state-in-effect is an error repo-wide', { timeout: ESLINT_BUDGET_MS }, () => {
  it('a planted violation lints as an error in files the old scope never gated', async () => {
    for (const file of EX_SCOPE) {
      expect(existsSync(`${FRONTEND}${file}`), `${file} is missing`).toBe(true);
      expect(await severity(file), file).toBe(2);
      expect(await ruleHits(eslint, PLANTED, file), file).toBe(1);
    }
  });

  it('the lane files lint clean at error', async () => {
    for (const file of LANE_FILES) {
      expect(await severity(file), file).toBe(2);
      const text = readFileSync(`${FRONTEND}${file}`, 'utf8') as string;
      expect(await ruleHits(eslint, text, file), file).toBe(0);
    }
  });

  it('every pending entry still reproduces a hit (a fixed file leaves the list)', async () => {
    const files = Object.keys(pending);
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) {
      expect(existsSync(`${FRONTEND}${file}`), `${file} is missing`).toBe(true);
      expect(pending[file], file).toMatch(/^w5-[a-z0-9-]+( \(residual partition\))?$/);
      expect(await severity(file), file).toBe(0);
      const text = readFileSync(`${FRONTEND}${file}`, 'utf8') as string;
      expect(await ruleHits(strict, text, file), `${file} no longer has a hit: remove its entry`).toBeGreaterThan(0);
    }
  });

  it('parks no file this lane owns', () => {
    for (const file of LANE_FILES) expect(Object.keys(pending), file).not.toContain(file);
  });

  it('never gates test files', async () => {
    expect(await severity('src/lib/mutations/outreach.test.ts')).toBe(0);
    expect(await severity('src/components/mortgage/ApprovalBanner.test.tsx')).toBe(0);
    expect(await ruleHits(eslint, PLANTED, 'src/components/mortgage/LeadTable.planted.test.tsx')).toBe(0);
  });
});
