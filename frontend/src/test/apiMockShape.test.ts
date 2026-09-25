/**
 * Whole-module lib/api mocks (the CI-flake class behind bde112b0, audit
 * 2026-09-21 wave 4).
 *
 * A factory `vi.mock('../lib/api', () => ({ api: {...} }))` REPLACES the
 * module: every other export (ApiError, isAbortError, dependencyLabel, ...)
 * becomes undefined for the whole test file. That passes until some module
 * the test loads, often a route chunk a sibling test preloaded, imports one
 * of them; then `x instanceof ApiError` throws "Right-hand side of
 * 'instanceof' is not callable" depending on test order, which is how it
 * flaked on CI. The fix keeps the real module and overrides only what the
 * test drives:
 *
 *   vi.mock('../lib/api', async (importOriginal) => ({
 *     ...(await importOriginal<typeof import('../lib/api')>()),
 *     api: apiMocks,
 *   }));
 *
 * This gate parses every src/**\/*.{test,test-support}.{ts,tsx} with the
 * TypeScript compiler, finds each vi.mock / vi.doMock whose specifier resolves
 * (relative to that file) to src/lib/api, and fails with file:line unless:
 *   - there is no factory (an automock keeps every export name), or
 *   - the returned object spreads the real module: `...(await importOriginal())`
 *     or `...(await vi.importActual(...))`, or an identifier bound to either
 *     in the factory body, or
 *   - the returned object defines ApiError (the wave-4 criterion; tightening
 *     it to "must spread" is wave 5, see docs/testing.md).
 * PENDING_PARTIAL_API_MOCKS is shrink-only: each entry must still fail.
 *
 * Node environment: parses sources, never runs them.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads test sources under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const FRONTEND = decodeURIComponent(new URL('../../', import.meta.url).pathname);
const SRC_ROOT = `${FRONTEND}src/`;
const TARGET = 'src/lib/api';

/**
 * SHRINK-ONLY. Test files a same-batch (W4b) lane owns, each still carrying a
 * bare factory; the owner converts it and the integrator retires the entry
 * (it then fails as stale). Never add a file: convert its factory.
 */
const PENDING_PARTIAL_API_MOCKS: Readonly<Record<string, { owner: string; reason: string }>> = {
  'src/components/mortgage/useLeadApproveReview.test.tsx': {
    owner: 'w4-lead-queue',
    reason: 'lead-queue owns this suite in W4b and converts its lib/api factory to the importOriginal spread',
  },
  'src/components/mortgage/useLeadSalesActions.test.tsx': {
    owner: 'w4-lead-queue',
    reason: 'lead-queue owns this suite in W4b and converts its lib/api factory to the importOriginal spread',
  },
  'src/components/mortgage/AssignmentLifecycleAdvance.test.tsx': {
    owner: 'w4-lead-queue',
    reason: 'lead-queue owns this suite in W4b (AssignmentLifecycleAdvance) and converts its lib/api factory',
  },
  'src/lib/routeMeta.test.tsx': {
    owner: 'w4-genie-client',
    reason: 'genie-client claims this suite in W4b for its askGenieConversation pins and converts the mock',
  },
};

type Verdict = 'automock' | 'spread' | 'api-error' | 'bare';

interface ApiMock {
  /** Repo-relative path of the test file. */
  file: string;
  line: number;
  verdict: Verdict;
}

/** posix-normalise `dir/spec`, dropping a TS/JS extension. */
function resolveSpecifier(fromFile: string, specifier: string): string | null {
  if (!specifier.startsWith('.')) return null;
  const parts = fromFile.split('/').slice(0, -1);
  for (const segment of specifier.split('/')) {
    if (segment === '' || segment === '.') continue;
    if (segment === '..') parts.pop();
    else parts.push(segment);
  }
  return parts.join('/').replace(/\.(tsx?|jsx?)$/, '');
}

function unwrap(node: ts.Expression): ts.Expression {
  let current = node;
  while (
    ts.isParenthesizedExpression(current) ||
    ts.isAsExpression(current) ||
    ts.isSatisfiesExpression(current) ||
    ts.isTypeAssertionExpression(current) ||
    ts.isNonNullExpression(current)
  ) {
    current = current.expression;
  }
  return current;
}

/** `await importOriginal(...)` (the factory's own parameter) or `await vi.importActual(...)`. */
function isRealModuleLoad(node: ts.Expression, importOriginal: string | null): boolean {
  const expression = unwrap(node);
  if (!ts.isAwaitExpression(expression)) return false;
  const call = unwrap(expression.expression);
  if (!ts.isCallExpression(call)) return false;
  const callee = call.expression;
  if (ts.isIdentifier(callee)) return importOriginal !== null && callee.text === importOriginal;
  return (
    ts.isPropertyAccessExpression(callee) &&
    ts.isIdentifier(callee.expression) &&
    callee.expression.text === 'vi' &&
    callee.name.text === 'importActual'
  );
}

/** Names bound, anywhere in the factory body, to a real-module load. */
function realModuleBindings(body: ts.Node, importOriginal: string | null): Set<string> {
  const bound = new Set<string>();
  const visit = (node: ts.Node): void => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer && isRealModuleLoad(node.initializer, importOriginal)) {
      bound.add(node.name.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(body);
  return bound;
}

/** Every object literal the factory can return. */
function returnedObjects(factory: ts.ArrowFunction | ts.FunctionExpression): ts.ObjectLiteralExpression[] {
  const objects: ts.ObjectLiteralExpression[] = [];
  const consider = (expression: ts.Expression | undefined): void => {
    if (!expression) return;
    const value = unwrap(expression);
    if (ts.isObjectLiteralExpression(value)) objects.push(value);
  };
  if (!ts.isBlock(factory.body)) {
    consider(factory.body);
    return objects;
  }
  const visit = (node: ts.Node): void => {
    // A nested function's returns are not the factory's.
    if (ts.isFunctionLike(node)) return;
    if (ts.isReturnStatement(node)) consider(node.expression);
    ts.forEachChild(node, visit);
  };
  ts.forEachChild(factory.body, visit);
  return objects;
}

function propertyName(member: ts.ObjectLiteralElementLike): string | null {
  if (ts.isShorthandPropertyAssignment(member)) return member.name.text;
  if (!('name' in member) || !member.name) return null;
  const name = member.name;
  if (ts.isIdentifier(name) || ts.isStringLiteral(name)) return name.text;
  return null;
}

function classifyFactory(factory: ts.Expression | undefined): Verdict {
  if (!factory) return 'automock';
  const fn = unwrap(factory);
  if (!ts.isArrowFunction(fn) && !ts.isFunctionExpression(fn)) return 'bare';
  const first = fn.parameters[0];
  const importOriginal = first && ts.isIdentifier(first.name) ? first.name.text : null;
  const bound = realModuleBindings(fn.body, importOriginal);
  const objects = returnedObjects(fn);
  if (objects.length === 0) return 'bare';
  const spreads = (object: ts.ObjectLiteralExpression): boolean =>
    object.properties.some((member) => {
      if (!ts.isSpreadAssignment(member)) return false;
      const spread = unwrap(member.expression);
      return isRealModuleLoad(spread, importOriginal) || (ts.isIdentifier(spread) && bound.has(spread.text));
    });
  if (objects.every(spreads)) return 'spread';
  if (objects.every((object) => object.properties.some((member) => propertyName(member) === 'ApiError'))) return 'api-error';
  return 'bare';
}

/** Every lib/api mock in one source text. `file` is repo-relative from frontend/ (src/...). */
function apiMocksIn(file: string, text: string): ApiMock[] {
  const kind = file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
  const mocks: ApiMock[] = [];
  const visit = (node: ts.Node): void => {
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      ts.isIdentifier(node.expression.expression) &&
      node.expression.expression.text === 'vi' &&
      (node.expression.name.text === 'mock' || node.expression.name.text === 'doMock')
    ) {
      const [specifier, factory] = node.arguments;
      if (specifier && ts.isStringLiteralLike(specifier) && resolveSpecifier(file, specifier.text) === TARGET) {
        const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
        mocks.push({ file, line: line + 1, verdict: classifyFactory(factory) });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return mocks;
}

function testSources(): Array<{ file: string; text: string }> {
  return (readdirSync(SRC_ROOT, { recursive: true }) as string[])
    .map((entry) => entry.split('\\').join('/'))
    .filter((entry) => /\.(test|test-support)\.tsx?$/.test(entry))
    .sort()
    .map((entry) => ({ file: `src/${entry}`, text: readFileSync(`${SRC_ROOT}${entry}`, 'utf8') as string }));
}

describe('lib/api mocks keep the real module (bde112b0)', () => {
  const mocks = testSources().flatMap(({ file, text }) => apiMocksIn(file, text));
  const failing = mocks.filter((mock) => mock.verdict === 'bare');

  it('finds the repo-wide mock population (non-vacuity)', () => {
    expect(mocks.length).toBeGreaterThanOrEqual(100);
    expect(mocks.some((mock) => mock.verdict === 'spread')).toBe(true);
    expect(mocks.some((mock) => mock.file.endsWith('.test-support.tsx'))).toBe(true);
  });

  it('classifies each factory shape (inline cases)', () => {
    const verdict = (file: string, text: string): Verdict[] => apiMocksIn(file, text).map((mock) => mock.verdict);
    expect(verdict('src/routes/x.test.tsx', "vi.mock('../lib/api', () => ({ api: { a: vi.fn() } }));")).toEqual(['bare']);
    expect(verdict('src/routes/x.test.tsx', "vi.mock('../lib/api');")).toEqual(['automock']);
    expect(
      verdict('src/routes/x.test.tsx', "vi.mock('../lib/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../lib/api')>()), api }));"),
    ).toEqual(['spread']);
    expect(
      verdict(
        'src/routes/x.test-support.tsx',
        "vi.mock('../lib/api', async () => { const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api'); return { ...actual, api }; });",
      ),
    ).toEqual(['spread']);
    expect(verdict('src/routes/x.test.tsx', "vi.mock('../lib/api', () => ({ ApiError: class extends Error {}, api }));")).toEqual(['api-error']);
    // A spread of something that is not the real module is still bare.
    expect(verdict('src/routes/x.test.tsx', "vi.mock('../lib/api', () => ({ ...shared, api }));")).toEqual(['bare']);
    // A sibling module is not lib/api.
    expect(verdict('src/routes/x.test.tsx', "vi.mock('../lib/apiTypes', () => ({ x: 1 }));")).toEqual([]);
    // Resolved relative to the file: two levels up from a nested directory.
    expect(verdict('src/components/mortgage/x.test.tsx', "vi.doMock('../../lib/api', () => ({ api }));")).toEqual(['bare']);
    expect(verdict('src/lib/x.test.ts', "vi.mock('./api', () => ({ api }));")).toEqual(['bare']);
    expect(verdict('src/lib/x.test.ts', "vi.mock('../api', () => ({ api }));")).toEqual([]);
    // A factory held in a variable cannot be proven to spread: it fails.
    expect(verdict('src/routes/x.test.tsx', "vi.mock('../lib/api', factory);")).toEqual(['bare']);
  });

  it('every lib/api factory spreads the real module or defines ApiError', () => {
    const offenders = failing
      .filter((mock) => !(mock.file in PENDING_PARTIAL_API_MOCKS))
      .map((mock) => `${mock.file}:${mock.line} replaces the whole module: spread (await importOriginal<typeof import(...)>())`);
    expect(offenders).toEqual([]);
  });

  it('PENDING_PARTIAL_API_MOCKS is shrink-only: every entry still fails', () => {
    const stale = Object.keys(PENDING_PARTIAL_API_MOCKS).filter((file) => !failing.some((mock) => mock.file === file));
    expect(stale, 'converted: retire these entries').toEqual([]);
  });
});
