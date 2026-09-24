/**
 * The fixture-data import rules (audit 2026-09-21 quality-09, step 3), proven
 * against the real frontend/eslint.config.js the way
 * setStateInEffectScope.test.ts proves its scope.
 *
 * tools/export_e2e_fixtures.mjs loads the fixture data modules on bare Node:
 * types are stripped, every surviving import is resolved. So in data/*.ts,
 * registry.ts, mockApi.ts and contractSamples.ts a package may be imported
 * only with `import type` (node: builtins aside), a src type imported as a
 * value is an error, and a type-only specifier must be spelled `type`.
 */
import { ESLint } from 'eslint';
import { beforeAll, describe, expect, it } from 'vitest';

const FRONTEND_URL = new URL('../../', import.meta.url);
/** Absolute frontend/ directory, with a trailing slash. */
const FRONTEND = decodeURIComponent(FRONTEND_URL.pathname);
const DATA_MODULE = `${FRONTEND}tests/e2e/fixture/data/__rule_probe__.ts`;
const SPEC_FILE = `${FRONTEND}tests/e2e/fixture/__rule_probe__.fixture.spec.ts`;

// Loading the config pulls in the typescript-eslint and react-hooks plugins;
// on a loaded CI box that alone can pass the unit-test defaults.
const ESLINT_BUDGET_MS = 60_000;

let eslint: ESLint;

beforeAll(() => {
  eslint = new ESLint({ cwd: FRONTEND });
}, ESLINT_BUDGET_MS);

async function errors(code: string, filePath: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath });
  return result.messages.filter((message) => message.severity === 2).map((message) => message.ruleId ?? '<parse>');
}

describe('fixture data import rules', { timeout: ESLINT_BUDGET_MS }, () => {
  it('rejects a runtime import of a package in a data module', async () => {
    const code = "import { expect } from '@playwright/test';\nexport const probe = expect;\n";
    expect(await errors(code, DATA_MODULE)).toEqual(['@typescript-eslint/no-restricted-imports']);
  });

  it('allows a type-only package import and a node: builtin', async () => {
    const code = [
      "import type { Page } from '@playwright/test';",
      "import { createHash } from 'node:crypto';",
      'export const probe = (page: Page | null) => (page ? createHash("sha256").digest("hex") : "");',
      '',
    ].join('\n');
    expect(await errors(code, DATA_MODULE)).toEqual([]);
  });

  it('still rejects a runtime import from frontend/src', async () => {
    const code = "import { scoreBand } from '../../../../src/lib/opportunityScore';\nexport const probe = scoreBand;\n";
    expect(await errors(code, DATA_MODULE)).toEqual(['@typescript-eslint/no-restricted-imports']);
  });

  it('requires `type` on a specifier used only as a type', async () => {
    const code = "import { json, FixtureEntry } from '../mockApi';\nexport const probe: FixtureEntry[] = [];\nexport const reply = json;\n";
    expect(await errors(code, DATA_MODULE)).toEqual(['@typescript-eslint/consistent-type-imports']);
  });

  it('leaves specs on the harness-wide rule only (a spec never runs on bare Node)', async () => {
    const code = "import { expect } from '@playwright/test';\nexport const probe = expect;\n";
    expect(await errors(code, SPEC_FILE)).toEqual([]);
  });
});
