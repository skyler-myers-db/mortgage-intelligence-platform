/**
 * One React act environment for the whole Vitest run (audit quality-06,
 * W5e part A).
 *
 * src/test/setup.ts, the suite's only setup file, sets
 * `IS_REACT_ACT_ENVIRONMENT = true` before every test file. Suites used to
 * repeat the assignment per file; a copy that drifts (sets it false, or sets
 * it after a render) silently changes what that suite tests, and 268 copies
 * hid which suites still leak "not wrapped in act(...)" warnings. This gate
 * parses every src/**\/*.{ts,tsx} with the TypeScript compiler and fails
 * with file:line on any assignment to IS_REACT_ACT_ENVIRONMENT (a property
 * write, an element write, or vi.stubGlobal) outside setup.ts.
 * tools/test_harness/h5_act_flag.py deletes the per-file line.
 *
 * Node environment: parses sources, never runs them.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads sources under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const FRONTEND = decodeURIComponent(new URL('../../', import.meta.url).pathname);
const SRC_ROOT = `${FRONTEND}src/`;
const SETUP = 'src/test/setup.ts';
const FLAG = 'IS_REACT_ACT_ENVIRONMENT';

interface Assignment {
  file: string;
  line: number;
}

function isFlagTarget(node: ts.Expression): boolean {
  if (ts.isParenthesizedExpression(node)) return isFlagTarget(node.expression);
  if (ts.isPropertyAccessExpression(node)) return node.name.text === FLAG;
  if (ts.isElementAccessExpression(node)) {
    const key = node.argumentExpression;
    return ts.isStringLiteralLike(key) && key.text === FLAG;
  }
  return false;
}

function isStubGlobal(node: ts.CallExpression): boolean {
  const callee = node.expression;
  const [name] = node.arguments;
  return (
    ts.isPropertyAccessExpression(callee) &&
    ts.isIdentifier(callee.expression) &&
    callee.expression.text === 'vi' &&
    callee.name.text === 'stubGlobal' &&
    name !== undefined &&
    ts.isStringLiteralLike(name) &&
    name.text === FLAG
  );
}

/** Every assignment to the act flag in one source text. */
function flagAssignments(file: string, text: string): Assignment[] {
  const kind = file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
  const found: Assignment[] = [];
  const visit = (node: ts.Node): void => {
    const assigns =
      (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken && isFlagTarget(node.left)) ||
      (ts.isCallExpression(node) && isStubGlobal(node));
    if (assigns) {
      const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
      found.push({ file, line: line + 1 });
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

function sources(): Array<{ file: string; text: string }> {
  return (readdirSync(SRC_ROOT, { recursive: true }) as string[])
    .map((entry) => entry.split('\\').join('/'))
    .filter((entry) => /\.tsx?$/.test(entry) && !entry.endsWith('.d.ts'))
    .sort()
    .map((entry) => ({ file: `src/${entry}`, text: readFileSync(`${SRC_ROOT}${entry}`, 'utf8') as string }));
}

describe('one React act environment (quality-06)', () => {
  const files = sources();
  const assignments = files.flatMap(({ file, text }) => flagAssignments(file, text));

  it('scans the source tree and finds setup.ts setting the flag (non-vacuity)', () => {
    expect(files.length).toBeGreaterThanOrEqual(1000);
    expect(assignments.filter((found) => found.file === SETUP)).toHaveLength(1);
  });

  it('sees each assignment shape and ignores reads and strings (inline cases)', () => {
    const count = (text: string): number => flagAssignments('src/x.test.tsx', text).length;
    expect(count('(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;')).toBe(1);
    expect(count('(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;')).toBe(1);
    expect(count("(globalThis as Record<string, unknown>)['IS_REACT_ACT_ENVIRONMENT'] = true;")).toBe(1);
    expect(count("vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);")).toBe(1);
    expect(count('const on = (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT === true;')).toBe(0);
    expect(count("const text = 'IS_REACT_ACT_ENVIRONMENT = true';")).toBe(0);
  });

  it('only src/test/setup.ts assigns IS_REACT_ACT_ENVIRONMENT', () => {
    const offenders = assignments
      .filter((found) => found.file !== SETUP)
      .map((found) => `${found.file}:${found.line} sets the act flag; src/test/setup.ts already does (tools/test_harness/h5_act_flag.py)`);
    expect(offenders).toEqual([]);
  });
});
