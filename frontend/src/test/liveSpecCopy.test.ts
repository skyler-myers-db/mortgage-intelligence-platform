/**
 * Live-spec copy gate (release_gate_bootstrap_findings.md, D-platform-process-b
 * step 7, W5e).
 *
 * The nightly live suite (nightly.yml "Run real-UC Playwright specs") is the
 * release gate's evidence, and it runs only on demand against a deployed app.
 * A live spec that pins copy the product never shipped (or renamed) fails
 * that metered run for a reason no PR saw. This gate reads the spec list from
 * the nightly step, parses each spec with the TypeScript compiler, and
 * extracts the string literals it asserts as visible copy: getByText,
 * getByRole's `name`, getByLabel, toHaveText, toContainText and `hasText`.
 * Substituted templates, regexes and strings under 6 characters are skipped.
 * Each literal must appear (case-insensitive, whitespace-normalized) in the
 * shipped-copy sources: frontend/src non-test, non-mocks .ts/.tsx/.json,
 * backend/**\/*.py and genie/**. Copy that only live DATA produces (a
 * borrower count, a server-built sentence) is listed in DATA_DERIVED_COPY
 * with its reason, and every entry must still be absent from those sources,
 * so a stale entry fails too.
 *
 * Fix a miss by repointing the pin at the shipped copy (keep its intent;
 * never delete the assertion), or, for data-derived copy, by an entry here.
 *
 * Node environment: parses sources, never runs them.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads sources under Vitest only.
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const FRONTEND = decodeURIComponent(new URL('../../', import.meta.url).pathname);
const REPO = `${FRONTEND}../`;
const NIGHTLY = `${REPO}.github/workflows/nightly.yml`;
const STEP_NAME = 'Run real-UC Playwright specs';
const MIN_LENGTH = 6;

/**
 * Reviewed: literals a live spec pins that only live data or a server-built
 * sentence produces, so no shipped source spells them. Literal -> reason.
 */
const DATA_DERIVED_COPY: Readonly<Record<string, string>> = {
  // economics_scatter.spec.ts route-fulfils a 120-borrower window under a 75-point cap.
  'Plotting 75 of 75 returned borrowers (120 total matching this window) (server cap 75)':
    'analytics.equity-scatter.tsx composes it from the payload counts (plotted, showing, matching, point_cap)',
  '120 borrowers; showing 75 ranked links returned under server cap':
    'analytics.equity-scatter.tsx composes it from coordinate_total and the returned link count',
  'Show 25 more': 'the scatter cluster pager composes "Show {n} more" from the rows left (CLUSTER_PAGE_SIZE)',
  // growth_agent_live.spec.ts types the state scope itself.
  'Invalid: illinois': "ask-genie.growth-agent-panel.tsx echoes the operator's own unparsed state tokens",
  'Scoped to IL, TX': "ask-genie.growth-agent-panel.tsx echoes the operator's own parsed states",
  // layout-stability.spec.ts serves its own E2E_LAYOUT_MOCK bodies.
  '71,553': "a count from the spec's own mocked portfolio preview",
  'State: IL': 'the Lead Queue scope pill composes "State: {stateFilter}" from the URL filter',
  'of 2 total matching filters': 'LeadTableFooter composes it from X-Total-Matching',
  'of 200 total matching filters': 'LeadTableFooter composes it from X-Total-Matching',
  // responsive.spec.ts reads the scatter legend, composed from the score-band constants.
  'High 85-100': 'ScoreBandLegend composes `${label} ${range}` from SCORE_BAND_HIGH_MIN',
  'Medium 65-84': 'ScoreBandLegend composes `${label} ${range}` from SCORE_BAND_MED_MIN and SCORE_BAND_HIGH_MIN',
  'Low 0-64': 'ScoreBandLegend composes `${label} ${range}` from SCORE_BAND_MED_MIN',
};

/** The spec paths (relative to frontend/) the nightly live step runs. */
function nightlyLiveSpecs(yaml: string): string[] {
  const start = yaml.indexOf(`- name: ${STEP_NAME}`);
  if (start === -1) return [];
  const rest = yaml.slice(start + 1);
  const next = rest.search(/\n\s+- name: /);
  const block = next === -1 ? rest : rest.slice(0, next);
  return [...block.matchAll(/(tests\/e2e\/[\w./-]+\.spec\.ts)/g)].map((match) => match[1]);
}

function literalText(node: ts.Node | undefined): string | null {
  if (!node) return null;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  return null;
}

const TEXT_ARG_CALLS = new Set(['getByText', 'getByLabel', 'toHaveText', 'toContainText']);

/** Every visible-copy literal one spec asserts, with its line. */
function assertedCopy(file: string, text: string): Array<{ literal: string; line: number }> {
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const found: Array<{ literal: string; line: number }> = [];
  const add = (node: ts.Node | undefined): void => {
    if (node && ts.isArrayLiteralExpression(node)) {
      node.elements.forEach(add);
      return;
    }
    const literal = literalText(node);
    if (literal === null || literal.trim().length < MIN_LENGTH || !node) return;
    const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
    found.push({ literal, line: line + 1 });
  };
  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node)) {
      const callee = node.expression;
      const name = ts.isPropertyAccessExpression(callee) ? callee.name.text : ts.isIdentifier(callee) ? callee.text : null;
      if (name && TEXT_ARG_CALLS.has(name)) add(node.arguments[0]);
      if (name === 'getByRole') {
        const options = node.arguments[1];
        if (options && ts.isObjectLiteralExpression(options)) {
          for (const property of options.properties) {
            if (ts.isPropertyAssignment(property) && ts.isIdentifier(property.name) && property.name.text === 'name') {
              add(property.initializer);
            }
          }
        }
      }
    }
    if (ts.isPropertyAssignment(node) && ts.isIdentifier(node.name) && node.name.text === 'hasText') add(node.initializer);
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/\s+/g, ' ').trim();
}

function walk(dir: string, keep: (path: string) => boolean, out: string[] = []): string[] {
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir) as string[]) {
    if (entry === 'node_modules' || entry === '__pycache__' || entry.startsWith('.')) continue;
    const path = `${dir}/${entry}`;
    if ((statSync(path) as { isDirectory: () => boolean }).isDirectory()) walk(path, keep, out);
    else if (keep(path)) out.push(path);
  }
  return out;
}

const isShippedFrontendSource = (path: string): boolean =>
  /\.(tsx?|json)$/.test(path) &&
  !/\.(test|test-support)\.tsx?$/.test(path) &&
  !path.includes('/src/test/') &&
  !path.includes('/src/mocks/');

/** The shipped-copy corpus, normalized, one string. */
function shippedCopy(): string {
  const files = [
    ...walk(`${FRONTEND}src`, isShippedFrontendSource),
    ...walk(`${REPO}backend`, (path) => path.endsWith('.py')),
    ...walk(`${REPO}genie`, () => true),
  ];
  return normalize(files.map((path) => readFileSync(path, 'utf8') as string).join('\n'));
}

describe('nightly live specs pin shipped copy (D-platform-process-b)', () => {
  const specs = nightlyLiveSpecs(readFileSync(NIGHTLY, 'utf8') as string);
  const corpus = shippedCopy();
  const pins = specs.flatMap((spec) =>
    assertedCopy(spec, readFileSync(`${FRONTEND}${spec}`, 'utf8') as string).map((pin) => ({ spec, ...pin })),
  );

  it('reads the nightly live step and finds its pins (non-vacuity)', () => {
    expect(specs.length).toBeGreaterThanOrEqual(8);
    expect(specs).toContain('tests/e2e/real_data.spec.ts');
    expect(pins.length).toBeGreaterThan(50);
    expect(corpus.length).toBeGreaterThan(1_000_000);
  });

  it('extracts each pinned-copy shape and skips templates, regexes and short strings (inline cases)', () => {
    const literals = (text: string): string[] => assertedCopy('x.spec.ts', text).map((pin) => pin.literal);
    expect(literals("await expect(page.getByText('Governed outreach')).toBeVisible();")).toEqual(['Governed outreach']);
    expect(literals("page.getByRole('button', { name: 'Approve draft' });")).toEqual(['Approve draft']);
    expect(literals("page.getByLabel('Search borrowers');")).toEqual(['Search borrowers']);
    expect(literals("await expect(row).toHaveText(['Refinance review', 'Pending']);")).toEqual(['Refinance review', 'Pending']);
    expect(literals("await expect(row).toContainText(`Exact audited copy`);")).toEqual(['Exact audited copy']);
    expect(literals("page.locator('.chip', { hasText: 'Refreshed today' });")).toEqual(['Refreshed today']);
    expect(literals('await expect(row).toContainText(`${count} borrowers`);')).toEqual([]);
    expect(literals('await expect(row).toHaveText(/Approve \\d+/);')).toEqual([]);
    expect(literals("page.getByText('Short');")).toEqual([]);
    expect(literals("page.locator('.not-copy-at-all');")).toEqual([]);
  });

  it('flags a literal the product never shipped (the gate is not vacuous)', () => {
    const planted = 'Zyrplax quarterly harmonics dashboard';
    expect(corpus.includes(normalize(planted))).toBe(false);
  });

  it('every pinned literal is shipped copy, or reviewed data-derived copy', () => {
    const misses = pins
      .filter((pin) => !(pin.literal in DATA_DERIVED_COPY))
      .filter((pin) => !corpus.includes(normalize(pin.literal)))
      .map((pin) => `${pin.spec}:${pin.line} pins copy no shipped source spells: ${JSON.stringify(pin.literal)}`);
    expect(misses).toEqual([]);
  });

  it('DATA_DERIVED_COPY holds no stale entry: each is still absent from the shipped sources', () => {
    const stale = Object.keys(DATA_DERIVED_COPY).filter((literal) => corpus.includes(normalize(literal)));
    expect(stale, 'now shipped copy: drop these entries').toEqual([]);
    const unused = Object.keys(DATA_DERIVED_COPY).filter((literal) => !pins.some((pin) => pin.literal === literal));
    expect(unused, 'no live spec pins these any more: drop them').toEqual([]);
  });
});
