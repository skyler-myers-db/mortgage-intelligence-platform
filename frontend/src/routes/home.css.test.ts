import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the stylesheet text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';

declare const process: { cwd(): string };

/** Declarations only: comments carry the breakpoint arithmetic. */
const read = (...path: string[]): string =>
  readFileSync(join(process.cwd(), 'src', ...path), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

const homeCss = (): string => read('routes', 'home.css');

/** Each `<number><unit>` term of a clamp(), so `1.20vw` and `1.2vw` compare equal. */
function terms(clamp: string): Array<[number, string]> {
  return [...clamp.matchAll(/(\d*\.?\d+)(rem|vw|px)/g)].map((term) => [Number(term[1]), term[2]]);
}

/** The body of the one `@container main` rule whose condition reads --fs-hero's clamp. */
function wrappedHero(): { condition: string; body: string } {
  const match = homeCss().match(/@container main \(max-width: (calc\([^{]*clamp\([^)]*\)\))\)\s*\{([\s\S]*?)\n\}/);
  expect(match, 'home.css has the wrapped-hero container query').not.toBeNull();
  return { condition: match![1], body: match![2] };
}

function rule(css: string, selector: RegExp): string {
  const match = css.match(new RegExp(`(?:^|\\n|\\})\\s*${selector.source}\\s*\\{([^}]*)\\}`));
  return match ? match[1] : '';
}

describe('home.css hero actions (deviation:home-fetched-at)', () => {
  it("the wrapped-hero breakpoint follows the title's --fs-hero, copied verbatim from tokens.css", () => {
    // A container condition cannot read var(), so the query spells the token
    // out; a change to --fs-hero must move the breakpoint with it.
    const token = read('design-system', 'tokens.css').match(/--fs-hero:\s*(clamp\([^;]*\));/);
    expect(token, 'tokens.css defines --fs-hero as a clamp()').not.toBeNull();
    const { condition } = wrappedHero();
    const copy = condition.match(/clamp\([^)]*\)/);
    expect(copy).not.toBeNull();
    expect(terms(copy![0])).toEqual(terms(token![1]));
    // padding + gap + the widest settled stack + slack, plus the title's 25.13em.
    expect(condition).toMatch(/^calc\(459px \+ 25\.13 \* clamp\(/);
  });

  it('wraps the stack into one start-aligned, centred row and lifts the 42vw cap on its own line', () => {
    const { body } = wrappedHero();
    expect(rule(body, /\.proto-hero__actions:has\(> \.home-hero\)/)).toMatch(/max-inline-size:\s*100%;/);
    const row = rule(body, /\.home-hero/);
    expect(row).toMatch(/flex-direction:\s*row;/);
    expect(row).toMatch(/flex-wrap:\s*wrap;/);
    expect(row).toMatch(/align-items:\s*center;/);
    expect(rule(body, /\.home-hero__controls/)).toMatch(/justify-content:\s*flex-start;/);
  });

  it('keeps the end-aligned two-row stack beside the title outside the query', () => {
    const outside = homeCss().replace(/@container[\s\S]*?\n\}/g, '');
    const stack = rule(outside, /\.home-hero/);
    expect(stack).toMatch(/flex-direction:\s*column;/);
    expect(stack).toMatch(/align-items:\s*flex-end;/);
    expect(rule(outside, /\.home-hero__controls/)).toMatch(/justify-content:\s*flex-end;/);
    for (const declarations of [stack, rule(outside, /\.home-hero__controls/)]) {
      expect(declarations).not.toMatch(/\d+px|#[0-9a-f]{3,8}\b/i);
    }
  });
});
