/**
 * No name on a role that prohibits one (WCAG 4.1.2; docs/accessibility/VPAT.md
 * open item 9, the post-W5 screen-reader pass).
 *
 * ARIA 1.2 prohibits aria-label and aria-labelledby on the generic role (a
 * plain div or span) and on caption, code, deletion, emphasis, insertion,
 * paragraph, presentation/none, strong, subscript and superscript. Screen
 * readers that follow the rule drop the name, so a "Message evidence" chip
 * row or an "Opportunity score 88 ..." badge reads as its bare text. axe
 * files such a name as needs-review while the element has text, so the
 * fixture axe gate's violations never showed the class; it took the virtual
 * screen-reader pass to find it (PageShell's footer, the approve review's
 * chip row), and a census found 25 more.
 *
 * Over every non-test .tsx under frontend/src, parsed with the TypeScript
 * compiler: an intrinsic element whose tag maps to one of those roles (or to
 * no role at all) and that carries aria-label or aria-labelledby must also
 * carry a role that permits a name:
 *  - a string-literal role outside PROHIBITED_ROLES; or
 *  - a conditional role whose every branch is such a literal, or is
 *    `undefined` only where the name, under the same condition, is
 *    `undefined` too (`role={empty ? undefined : 'group'}` with
 *    `aria-label={empty ? undefined : 'Active filters'}`); or
 *  - a spread of a REVIEWED_ROLE_SPREADS prop bag, whose type fixes its role.
 * Anything else (a dynamic role this scan cannot read) fails here: give it a
 * literal role or move the name into visually hidden text (`.sr-only`).
 *
 * Not covered, and why: header and footer (their role is banner/contentinfo
 * or generic depending on where they sit, which source cannot tell; the axe
 * gate's needs-review check in tests/e2e/fixture/axe.ts sees them in the
 * rendered page), and a name passed through a component's prop spread into a
 * div (the same axe check). Node environment: reads files only.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the source tree under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const FRONTEND = decodeURIComponent(new URL('../../', import.meta.url).pathname);

/** Tags whose implicit role prohibits a name, or that map to no role (HTML-AAM). */
export const UNNAMEABLE_TAGS: ReadonlySet<string> = new Set([
  'abbr', 'b', 'bdi', 'bdo', 'code', 'data', 'del', 'div', 'em', 'i', 'ins', 'kbd', 'label',
  'p', 'pre', 'q', 's', 'samp', 'small', 'span', 'strong', 'sub', 'sup', 'u', 'var',
]);

/** Roles on which ARIA 1.2 prohibits aria-label and aria-labelledby. */
export const PROHIBITED_ROLES: ReadonlySet<string> = new Set([
  'caption', 'code', 'deletion', 'emphasis', 'generic', 'insertion', 'none', 'paragraph',
  'presentation', 'strong', 'subscript', 'superscript',
]);

/**
 * Prop bags spread onto a named element whose TYPE fixes a permitted role.
 * `tabs.tabListProps` is ui/useTabs' `{ role: 'tablist'; ... }`.
 */
export const REVIEWED_ROLE_SPREADS: ReadonlySet<string> = new Set(['tabs.tabListProps']);

const NAME_ATTRIBUTES = ['aria-label', 'aria-labelledby'] as const;

interface Source {
  path: string;
  text: string;
}

function isTestPath(path: string): boolean {
  return /\.(test|spec|test-support|stories)\.[cm]?[jt]sx?$/.test(path) || path.startsWith('src/test/');
}

function readSources(): Source[] {
  return (readdirSync(`${FRONTEND}src/`, { recursive: true }) as string[])
    .map((entry) => `src/${entry.split('\\').join('/')}`)
    .filter((path) => path.endsWith('.tsx') && !isTestPath(path))
    .sort()
    .map((path) => ({ path, text: readFileSync(`${FRONTEND}${path}`, 'utf8') as string }));
}

/** A JSX attribute value as one leaf (a literal role, `undefined`, or unreadable) or a condition over two. */
type Leaf = { kind: 'literal'; value: string } | { kind: 'undefined' } | { kind: 'unknown' };
type Value = Leaf | { kind: 'conditional'; condition: string; whenTrue: Value; whenFalse: Value };

function valueOf(expression: ts.Expression, file: ts.SourceFile): Value {
  let node = expression;
  while (ts.isParenthesizedExpression(node)) node = node.expression;
  if (ts.isStringLiteralLike(node)) return { kind: 'literal', value: node.text };
  if (ts.isIdentifier(node) && node.text === 'undefined') return { kind: 'undefined' };
  if (ts.isConditionalExpression(node)) {
    return {
      kind: 'conditional',
      condition: node.condition.getText(file),
      whenTrue: valueOf(node.whenTrue, file),
      whenFalse: valueOf(node.whenFalse, file),
    };
  }
  return { kind: 'unknown' };
}

function attributeValue(attribute: ts.JsxAttribute, file: ts.SourceFile): Value {
  const initializer = attribute.initializer;
  if (initializer === undefined) return { kind: 'unknown' };
  if (ts.isStringLiteral(initializer)) return { kind: 'literal', value: initializer.text };
  if (ts.isJsxExpression(initializer) && initializer.expression) return valueOf(initializer.expression, file);
  return { kind: 'unknown' };
}

/**
 * Whether `role` permits the name `name` in every case. A role branch that
 * leaves the element unnamed-only (undefined or a prohibited literal) is
 * fine only where the name, under the same condition, is undefined too.
 */
function rolePermitsName(role: Value, name: Value): boolean {
  if (role.kind === 'literal') return !PROHIBITED_ROLES.has(role.value);
  if (role.kind === 'undefined') return name.kind === 'undefined';
  if (role.kind === 'unknown') return false;
  const paired = name.kind === 'conditional' && name.condition === role.condition;
  return (
    rolePermitsName(role.whenTrue, paired ? name.whenTrue : name) &&
    rolePermitsName(role.whenFalse, paired ? name.whenFalse : name)
  );
}

function isReviewedSpread(attribute: ts.JsxAttributeLike, file: ts.SourceFile): boolean {
  return ts.isJsxSpreadAttribute(attribute) && REVIEWED_ROLE_SPREADS.has(attribute.expression.getText(file));
}

/** Every named element in `source` whose role does not permit its name, as `path:line <tag> attribute`. */
export function prohibitedNames(source: Source): string[] {
  const file = ts.createSourceFile(source.path, source.text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found: string[] = [];
  const visit = (node: ts.Node): void => {
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && ts.isIdentifier(node.tagName)) {
      const tag = node.tagName.text;
      if (UNNAMEABLE_TAGS.has(tag)) {
        const attributes = node.attributes.properties;
        const byName = new Map<string, ts.JsxAttribute>();
        for (const attribute of attributes) {
          if (ts.isJsxAttribute(attribute)) byName.set(attribute.name.getText(file), attribute);
        }
        const role = byName.get('role');
        const reviewedSpread = attributes.some((attribute) => isReviewedSpread(attribute, file));
        for (const nameAttribute of NAME_ATTRIBUTES) {
          const named = byName.get(nameAttribute);
          if (named === undefined) continue;
          const name = attributeValue(named, file);
          const permitted = role ? rolePermitsName(attributeValue(role, file), name) : reviewedSpread || name.kind === 'undefined';
          if (!permitted) {
            const line = file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
            found.push(`${source.path}:${line} <${tag}> ${nameAttribute}`);
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

const SOURCES: readonly Source[] = readSources();

describe('no aria-label or aria-labelledby on a role that prohibits a name', () => {
  it('every named generic-tag element in frontend/src carries a role that permits its name', () => {
    expect(SOURCES.flatMap(prohibitedNames)).toEqual([]);
  });

  it('reads the real tree: the shell, the drawer and the cards are in it', () => {
    const paths = new Set(SOURCES.map((source) => source.path));
    for (const path of [
      'src/components/layout/Topbar.tsx',
      'src/components/mortgage/EvidenceDrawerBody.tsx',
      'src/components/mortgage/ScoreBadge.tsx',
      'src/routes/offer-orchestrator.panels.tsx',
    ]) {
      expect(paths.has(path), `${path} is scanned`).toBe(true);
    }
    expect(SOURCES.length).toBeGreaterThan(150);
  });

  it('each reviewed spread still fixes its role in its type', () => {
    const tabs = readFileSync(`${FRONTEND}src/components/ui/useTabs.ts`, 'utf8') as string;
    expect(tabs).toMatch(/tabListProps:\s*\{\s*role:\s*'tablist';/);
  });
});

describe('non-vacuity: injected sources', () => {
  const scan = (text: string) => prohibitedNames({ path: 'src/routes/injected.tsx', text });

  it('flags a named div, span and p with no role (aria-label and aria-labelledby)', () => {
    expect(
      scan(
        [
          'export const A = () => <div className="chip-row" aria-label="Message evidence" />;',
          'export const B = () => <span aria-labelledby="t">x</span>;',
          'export const C = () => <p aria-label={`Score ${1}`}>1</p>;',
        ].join('\n'),
      ),
    ).toEqual([
      'src/routes/injected.tsx:1 <div> aria-label',
      'src/routes/injected.tsx:2 <span> aria-labelledby',
      'src/routes/injected.tsx:3 <p> aria-label',
    ]);
  });

  it('flags a prohibited literal role, a dynamic role it cannot read and an unreviewed spread', () => {
    expect(
      scan(
        [
          'export const A = () => <div role="presentation" aria-label="x" />;',
          'export const B = ({ r }: { r: string }) => <div role={r} aria-label="x" />;',
          'export const C = (p: object) => <div {...p} aria-label="x" />;',
          'export const D = ({ on }: { on: boolean }) => <div role={on ? "group" : undefined} aria-label="x" />;',
        ].join('\n'),
      ),
    ).toHaveLength(4);
  });

  it('passes permitted roles, a condition that names only with a role, a reviewed spread and other tags', () => {
    expect(
      scan(
        [
          'export const A = () => <div role="group" aria-label="Message evidence" />;',
          'export const B = () => <span role="img" aria-label="Segment gated" />;',
          'export const C = ({ e }: { e: boolean }) => <div role={e ? undefined : "group"} aria-label={e ? undefined : "Active filters"} />;',
          'export const D = () => <div {...tabs.tabListProps} aria-label="Views" />;',
          'export const E = () => <section aria-label="Region" />;',
          'export const F = () => <button aria-label="Close" />;',
          'export const G = () => <div aria-hidden="true" />;',
        ].join('\n'),
      ),
    ).toEqual([]);
  });
});
