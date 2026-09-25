/**
 * Source contracts for the route + theme View Transitions sheet
 * (app.transitions.css; 2026-09-21 audit stack-04 / motion-03 / runtime-10 /
 * css-10 / shell-10, phase 1). The rendered proofs are
 * tests/e2e/fixture/motion-nav.fixture.spec.ts; these pin the rules those
 * proofs depend on, and the cascade position they must survive: main.tsx
 * imports this sheet (through appRouter / app.tsx) BEFORE the design-system
 * partials, so every override must win on specificity.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads stylesheet text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { designCss } from './test/designCss';
import { mediaBlocks, readPrintCss, readTokensCss, topLevelRules } from './test/tokenCascade';

declare const process: { cwd(): string };

const sheet = readFileSync(join(process.cwd(), 'src', 'app.transitions.css'), 'utf8') as string;
const tokens = readTokensCss();

/** Inner text of every `@supports <condition> { ... }` block. */
function supportsBlocks(css: string, condition: string): string[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const blocks: string[] = [];
  for (const match of text.matchAll(new RegExp(`@supports\\s+${condition}\\s*\\{`, 'g'))) {
    const start = (match.index ?? 0) + match[0].length;
    let depth = 1;
    let j = start;
    while (j < text.length && depth > 0) {
      if (text[j] === '{') depth += 1;
      else if (text[j] === '}') depth -= 1;
      j += 1;
    }
    blocks.push(text.slice(start, j - 1));
  }
  return blocks;
}

/** [ids, classes + attributes + pseudo-classes, types] of one simple compound-chain selector. */
function specificity(selector: string): [number, number, number] {
  const ids = (selector.match(/#[\w-]+/g) ?? []).length;
  const classes = (selector.match(/\.[\w-]+|\[[^\]]+\]|:(?!:)[\w-]+/g) ?? []).length;
  const types = (selector.replace(/[.#:[][^\s>+~]*/g, ' ').match(/[a-z][\w-]*/gi) ?? []).length;
  return [ids, classes, types];
}

function declarationsOf(css: string, selector: string): string {
  return topLevelRules(css).filter((rule) => rule.selector.replace(/\s+/g, ' ') === selector).map((rule) => rule.block).join(';');
}

describe('app.transitions.css (View Transitions phase 1)', () => {
  it('times every animation on the motion tokens, all of them defined', () => {
    const text = sheet.replace(/\/\*[\s\S]*?\*\//g, '');
    const motion = [...text.matchAll(/(?:^|[;{\s])(animation(?:-[a-z-]+)?|transition(?:-[a-z-]+)?)\s*:\s*([^;}]+)/g)];
    expect(motion.length).toBeGreaterThan(0);
    for (const [, property, value] of motion) {
      expect(value, `${property}: ${value}`).not.toMatch(/(?<![\w.-])\d*\.?\d+m?s(?![\w-])/);
      expect(value, `${property}: ${value}`).not.toMatch(/(?<![\w-])(?:ease(?:-in-out|-in|-out)?|linear)(?![\w(-])|cubic-bezier\(/);
    }
    const referenced = new Set([...text.matchAll(/var\((--(?:dur|ease)[\w-]*)\)/g)].map((match) => match[1]));
    expect([...referenced].sort()).toEqual(['--dur-base', '--dur-fast', '--ease', '--ease-exit']);
    for (const name of referenced) expect(tokens, `${name} is defined`).toMatch(new RegExp(`${name}:\\s*[^;]+;`));
  });

  it('animates only the route classes, and keeps them at or under 250 ms', () => {
    expect(declarationsOf(sheet, '::view-transition-old(.mip-route-exit)')).toMatch(/animation:\s*mip-route-exit var\(--dur-fast\) var\(--ease-exit\) both/);
    expect(declarationsOf(sheet, '::view-transition-new(.mip-route-enter)')).toMatch(/animation:\s*mip-route-enter var\(--dur-base\) var\(--ease\) both/);
    expect(declarationsOf(sheet, '::view-transition-group(*)')).toMatch(/animation-duration:\s*var\(--dur-base\)/);
    // --dur-base is the longest duration used; it stays under the 250 ms ceiling.
    expect(Number.parseFloat(/--dur-base:\s*(\d+)ms/.exec(tokens)?.[1] ?? 'NaN')).toBeLessThanOrEqual(250);
  });

  it('lets a click during a transition reach the live page', () => {
    expect(declarationsOf(sheet, '::view-transition')).toMatch(/pointer-events:\s*none/);
  });

  it('keeps the shell (the open Genie panel included) live on a route change, and cross-fades it only for a theme switch', () => {
    expect(declarationsOf(sheet, ':root:not([data-theme-switching])::view-transition-old(root)')).toMatch(/display:\s*none/);
    expect(declarationsOf(sheet, ':root:not([data-theme-switching])::view-transition-new(root)')).toMatch(/animation:\s*none/);
    // No element outside the route boundary gets a name of its own: Chromium
    // does not hit-test a named element mid-transition (the composer click
    // fell through to .main), and a duplicate name would abort the transition.
    expect(sheet.replace(/\/\*[\s\S]*?\*\//g, '')).not.toMatch(/view-transition-name:\s*(?!none)[\w-]+/);
  });

  it('gates route-in on missing View Transitions, above the (0,1,0) rule that sets it', () => {
    // The rule it overrides, from the design-system partials later in the cascade.
    expect(designCss()).toMatch(/\n\.route-transition\s*\{\s*animation:\s*route-in var\(--dur-base\) var\(--ease\);/);
    const gated = supportsBlocks(sheet, '\\(view-transition-name:\\s*none\\)');
    expect(gated).toHaveLength(1);
    const rules = topLevelRules(gated[0]);
    expect(rules.map((rule) => [rule.selector, rule.block.trim()])).toEqual([
      [':root .route-transition:not(.route-transition--fallback)', 'animation: none;'],
    ]);
    // :root + .route-transition + the :not() argument's class: above (0,1,0).
    expect(specificity(rules[0].selector.replace(':not(', ' ').replace(')', ''))).toEqual([0, 3, 0]);
    // Never gated on transition STATE: that replays route-in when it ends.
    expect(sheet.replace(/\/\*[\s\S]*?\*\//g, '')).not.toMatch(/active-view-transition/);
  });

  it('never animates the Suspense fallback wrapper, in any browser (its own rule, outside the gate)', () => {
    expect(declarationsOf(sheet, '.route-transition.route-transition--fallback')).toMatch(/animation:\s*none/);
    expect(specificity('.route-transition.route-transition--fallback')).toEqual([0, 2, 0]);
  });

  it('freezes colour transitions while the theme cross-fades', () => {
    expect(declarationsOf(sheet, ':root[data-theme-switching] :is(*, *::before, *::after)')).toMatch(/transition:\s*none !important/);
  });

  it('stops every view-transition pseudo under reduced motion', () => {
    const reduced = mediaBlocks(sheet, '\\(prefers-reduced-motion:\\s*reduce\\)');
    expect(reduced).toHaveLength(1);
    const rules = topLevelRules(reduced[0]);
    expect(rules).toHaveLength(1);
    expect(rules[0].selector.split(',').map((part) => part.trim())).toEqual([
      '::view-transition-group(*)',
      '::view-transition-old(*)',
      '::view-transition-new(*)',
    ]);
    expect(rules[0].block).toMatch(/animation:\s*none !important/);
  });

  it('never replays the route entrance on paper', () => {
    const print = mediaBlocks(readPrintCss(), 'print');
    expect(print).toHaveLength(1);
    const routeRules = topLevelRules(print[0]).filter((rule) => rule.selector === '.route-transition');
    expect(routeRules.map((rule) => rule.block.trim())).toEqual(['animation: none;']);
  });
});
