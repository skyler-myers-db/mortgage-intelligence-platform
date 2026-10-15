/**
 * The dependency-free BEM selector gate (2026-09-21 audit quality-10 step 3;
 * tools/lint_css_literals.mjs, run by `npm run lint`). Proven on the live
 * stylesheets and on synthetic CSS:
 *   - every class selector in a rule prelude under frontend/src follows the
 *     prototype BEM grammar, or is a listed exception;
 *   - each exception still occurs (shrink-only) and cites its reason;
 *   - camelCase, a__b__c, snake_case and upper case fail;
 *   - at-rule preludes (@keyframes percentages, @layer, @media, @supports)
 *     are not selectors, and comments and strings are ignored.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import { importUntyped, type NodeFs } from '../test/importUntyped';

interface Violation { file: string; line: number; name: string }
interface Gate {
  BEM_CLASS: RegExp;
  BEM_EXCEPTIONS: Record<string, string>;
  extractClassSelectors: (text: string) => Array<{ name: string; line: number }>;
  findBemViolationsInText: (text: string, file: string, exceptions?: Record<string, string>) => Violation[];
  findBemViolations: () => Violation[];
  frontendStylesheets: () => string[];
}

const REPO = decodeURIComponent(new URL('../../../', import.meta.url).pathname);

let gate: Gate;
let fs: NodeFs;

beforeAll(async () => {
  gate = await importUntyped<Gate>(new URL('../../../tools/lint_css_literals.mjs', import.meta.url));
  fs = await importUntyped<NodeFs>('node:fs');
});

const names = (css: string) => gate.extractClassSelectors(css).map((found) => found.name);
const violations = (css: string) => gate.findBemViolationsInText(css, 'probe.css', {}).map((found) => found.name);

// Reading ~90 stylesheets twice can pass the 5 s default on a loaded box.
describe('BEM selector gate: the live tree', { timeout: 60_000 }, () => {
  it('is clean over every frontend stylesheet', () => {
    expect(gate.frontendStylesheets().length).toBeGreaterThan(50);
    expect(gate.findBemViolations()).toEqual([]);
  });

  it('keeps each exception only while it still occurs, each with a cited reason', () => {
    const live = new Set(
      gate.frontendStylesheets().flatMap((file) => names(fs.readFileSync(`${REPO}${file}`, 'utf8'))),
    );
    for (const [name, reason] of Object.entries(gate.BEM_EXCEPTIONS)) {
      expect(live.has(name), `${name} no longer occurs: delete its BEM_EXCEPTIONS entry`).toBe(true);
      expect(gate.BEM_CLASS.test(name), `${name} conforms: it needs no exception`).toBe(false);
      expect(reason, name).toMatch(/design_files\/[^:]+:\d+|deviation:[a-z0-9-]+/);
    }
  });
});

describe('BEM selector gate: the grammar', () => {
  it('accepts block, element and modifier forms', () => {
    expect(violations('.kpi__value, .chip--success, .seg-card__count--compact, .btn.is-active, .h-4, .mt-3 {}')).toEqual([]);
    expect(violations('.tbl__expand-inner .score--high:not(.conf__bars) > .a {}')).toEqual([]);
  });

  it('rejects camelCase, a double element, snake_case and upper case', () => {
    expect(violations('.layoutB {}')).toEqual(['layoutB']);
    expect(violations('.card__body__title {}')).toEqual(['card__body__title']);
    expect(violations('.lead_table {}')).toEqual(['lead_table']);
    expect(violations('.Chip {}')).toEqual(['Chip']);
    expect(violations('.chip--Success {}')).toEqual(['chip--Success']);
    expect(violations('.a:has(.badName) {}')).toEqual(['badName']);
  });

  it('reads nested rules and at-rule groups, and ignores at-rule preludes', () => {
    const css = `
      @layer base, components;
      @layer components {
        @media (min-width: 40rem) { .rail__item { color: var(--text-1); } }
        @supports (selector(:has(.notAClass))) { .drawer--open { inset: 0; } }
      }
      @keyframes fadeIn { 0% { opacity: 0; } 50.5% { opacity: 0.5; } to { opacity: 1; } }
      @property --tooltipOffset { syntax: '<length>'; inherits: false; initial-value: 0px; }
      .genie { &.is-open { opacity: 1; } .genie__hdr { display: flex; } }
    `;
    expect(names(css)).toEqual(['rail__item', 'drawer--open', 'genie', 'is-open', 'genie__hdr']);
    expect(violations(css)).toEqual([]);
  });

  it('ignores comments, strings, attribute values and decimals', () => {
    const css = `
      /* .commentedOut { } */
      .chip::before { content: ".notAClass"; }
      a[href$=".PDF"] .chip__label { opacity: .5; }
      .grid { grid-template-areas: "a.b c"; }
    `;
    expect(names(css)).toEqual(['chip', 'chip__label', 'grid']);
  });

  it('reports the line of a violation', () => {
    const found = gate.findBemViolationsInText('.ok {}\n\n.fooBar,\n.baz {}', 'probe.css', {});
    expect(found).toEqual([{ file: 'probe.css', line: 3, name: 'fooBar' }]);
  });
});
