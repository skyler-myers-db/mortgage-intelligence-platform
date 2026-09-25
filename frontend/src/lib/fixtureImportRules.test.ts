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
 *
 * Node erases only a whole `import type` / `export type` declaration: an
 * all-inline `import { type A } from 'x'` is kept as `import {} from 'x'` and
 * `export { type A } from 'x'` as `export {} from 'x'`, both runtime loads of
 * 'x' that no-restricted-imports' allowTypeImports lets through. Those two
 * spellings, and import(), are errors here; a mixed `{ value, type A }`
 * import from a fixture module is fine (that module loads either way).
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
let fixer: ESLint;

beforeAll(() => {
  eslint = new ESLint({ cwd: FRONTEND });
  fixer = new ESLint({ cwd: FRONTEND, fix: true });
}, ESLINT_BUDGET_MS);

async function errors(code: string, filePath: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath });
  return result.messages.filter((message) => message.severity === 2).map((message) => message.ruleId ?? '<parse>');
}

/** `eslint --fix` output for `code`, and the errors left after the fix. */
async function autofix(code: string, filePath: string): Promise<{ output: string; remaining: string[] }> {
  const [result] = await fixer.lintText(code, { filePath });
  return {
    output: result.output ?? code,
    remaining: result.messages.filter((message) => message.severity === 2).map((message) => message.ruleId ?? '<parse>'),
  };
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

  it('allows an inline `type` beside a value import from a fixture module', async () => {
    const code = "import { json, type FixtureEntry } from '../mockApi';\nexport const probe: FixtureEntry[] = [];\nexport const reply = json;\n";
    expect(await errors(code, DATA_MODULE)).toEqual([]);
  });

  it('rejects an all-inline type import of a package (Node keeps it as a runtime import)', async () => {
    const code = "import { type Page } from '@playwright/test';\nexport const probe = (p: Page | null) => p;\n";
    expect(await errors(code, DATA_MODULE)).toEqual(['@typescript-eslint/no-import-type-side-effects']);
  });

  it('rejects an all-inline type import from frontend/src (Node would load src at runtime)', async () => {
    const code = "import { type Lead } from '../../../../src/types';\nexport const probe = (p: Lead | null) => p;\n";
    expect(await errors(code, DATA_MODULE)).toEqual(['@typescript-eslint/no-import-type-side-effects']);
  });

  it('autofixes a type used as a value import to `import type`, never to the inline form', async () => {
    const code = "import { Page } from '@playwright/test';\nexport const probe = (p: Page | null) => p;\n";
    expect(await autofix(code, DATA_MODULE)).toEqual({
      output: "import type { Page } from '@playwright/test';\nexport const probe = (p: Page | null) => p;\n",
      remaining: [],
    });
  });

  it('rejects an all-inline type re-export, and allows `export type`', async () => {
    expect(await errors("export { type Page } from '@playwright/test';\n", DATA_MODULE)).toEqual(['no-restricted-syntax']);
    expect(await errors("export { type Lead } from '../../../../src/types';\n", DATA_MODULE)).toEqual(['no-restricted-syntax']);
    expect(await errors("export type { Page } from '@playwright/test';\n", DATA_MODULE)).toEqual([]);
    expect(await errors("export { json, type FixtureEntry } from '../mockApi';\n", DATA_MODULE)).toEqual([]);
  });

  it('rejects a dynamic import()', async () => {
    const code = "export const probe = () => import('./genie');\n";
    expect(await errors(code, DATA_MODULE)).toEqual(['no-restricted-syntax']);
  });

  it('leaves specs on the harness-wide rule only (a spec never runs on bare Node)', async () => {
    const code = "import { expect } from '@playwright/test';\nexport const probe = expect;\n";
    expect(await errors(code, SPEC_FILE)).toEqual([]);
  });
});
