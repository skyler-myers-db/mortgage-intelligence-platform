/**
 * Ruling D-audit-reads-b (audit delivery-08, wave 5): RECOMMEND_OFFER is the
 * approval-surface open record, written once per Offer open by the ONE
 * observer in offer-orchestrator.queries.ts. A second caller of
 * api.recommendOffer (a prefetch, a hover, another surface) would write the
 * record for something no approver opened, so this pins the token
 * `recommendOffer` (identifier, member or quoted key; comments and template
 * text do not count) in non-test frontend/src sources to exactly its
 * definition in lib/apiClients/leads.ts and its call in the queries module.
 * Keyed by file path and occurrence count, never line numbers.
 *
 * Node environment: parses sources with the TypeScript compiler, never runs them.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads sources under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const SRC_ROOT = decodeURIComponent(new URL('../', import.meta.url).pathname);
const TOKEN = 'recommendOffer';
const EXPECTED: Readonly<Record<string, number>> = {
  'lib/apiClients/leads.ts': 1,
  'routes/offer-orchestrator.queries.ts': 1,
};

/** Identifier and string-literal nodes spelling `name` (comments and template text never are). */
function tokenCount(source: string, name: string, jsx: boolean): number {
  const file = ts.createSourceFile(jsx ? 'x.tsx' : 'x.ts', source, ts.ScriptTarget.Latest, false, jsx ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  let count = 0;
  const visit = (node: ts.Node): void => {
    if ((ts.isIdentifier(node) || ts.isStringLiteral(node)) && node.text === name) count += 1;
    ts.forEachChild(node, visit);
  };
  visit(file);
  return count;
}

function isProductionSource(rel: string): boolean {
  return (
    /\.(ts|tsx)$/.test(rel) &&
    !/\.d\.ts$/.test(rel) &&
    !/\.(test|test-support)\.(ts|tsx)$/.test(rel) &&
    !rel.startsWith('test/') &&
    !rel.startsWith('mocks/')
  );
}

function callerCounts(files: Readonly<Record<string, string>>): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const [rel, source] of Object.entries(files)) {
    if (!isProductionSource(rel)) continue;
    const count = tokenCount(source, TOKEN, rel.endsWith('.tsx'));
    if (count > 0) counts[rel] = count;
  }
  return counts;
}

function productionSources(): Record<string, string> {
  const files: Record<string, string> = {};
  for (const rel of (readdirSync(SRC_ROOT, { recursive: true }) as string[]).map((p) => p.split('\\').join('/'))) {
    if (isProductionSource(rel)) files[rel] = readFileSync(`${SRC_ROOT}${rel}`, 'utf8') as string;
  }
  return files;
}

describe('api.recommendOffer has one definition and one caller', () => {
  it('appears only in lib/apiClients/leads.ts and routes/offer-orchestrator.queries.ts', () => {
    const files = productionSources();
    expect(Object.keys(files).length, 'non-vacuity: the walk sees frontend/src').toBeGreaterThan(100);
    expect(callerCounts(files)).toEqual(EXPECTED);
  });

  it('flags a planted second caller, a quoted key and a destructured binding, and ignores comments and tests', () => {
    const planted = {
      'lib/apiClients/leads.ts': "export const leads = { recommendOffer: (id: string) => id };",
      'routes/offer-orchestrator.queries.ts': 'const q = () => api.recommendOffer(id); // recommendOffer once',
      'routes/borrower-360.prefetch.ts': "void api['recommendOffer']('B-0000000000000');",
      'components/HoverCard.tsx': 'const { recommendOffer } = api; export const H = () => <b>{String(recommendOffer)}</b>;',
      'lib/notes.ts': '/* api.recommendOffer is the open record */ export const n = `recommendOffer`;',
      'routes/offer-orchestrator.route.test.tsx': 'api.recommendOffer(id);',
      'mocks/fixtureData.ts': 'recommendOffer();',
    };
    expect(callerCounts(planted)).toEqual({
      ...EXPECTED,
      'routes/borrower-360.prefetch.ts': 1,
      'components/HoverCard.tsx': 2,
    });
  });
});
