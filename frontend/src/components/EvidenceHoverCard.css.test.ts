/**
 * Source contracts for the evidence hover card sheet (EvidenceHoverCard.css;
 * re-audit #4 Buyer-Wow #8, 2026-09-21 audit motion-10 / css-10). The four
 * original pins moved here from design-system/components.test.ts with the
 * block; the rendered proofs are
 * tests/e2e/fixture/motion-nav.hovercard.fixture.spec.ts.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads stylesheet text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { designCss } from '../test/designCss';
import { mediaBlocks, topLevelRules } from '../test/tokenCascade';

declare const process: { cwd(): string };

const css = readFileSync(join(process.cwd(), 'src', 'components', 'EvidenceHoverCard.css'), 'utf8') as string;

function rule(selector: string, source = css): string {
  return topLevelRules(source)
    .filter((entry) => entry.selector.replace(/\s+/g, ' ') === selector)
    .map((entry) => entry.block)
    .join(';');
}

function supportsBlock(condition: string): string {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const match = new RegExp(`@supports\\s+${condition}\\s*\\{`).exec(text);
  if (!match) return '';
  let depth = 1;
  let j = (match.index ?? 0) + match[0].length;
  const start = j;
  while (j < text.length && depth > 0) {
    if (text[j] === '{') depth += 1;
    else if (text[j] === '}') depth -= 1;
    j += 1;
  }
  return text.slice(start, j - 1);
}

describe('EvidenceHoverCard.css', () => {
  it('keeps the card fixed and click-transparent (moved pins, re-audit #4 #8)', () => {
    expect(rule('.evidence-hovercard')).toMatch(/position:\s*fixed;/);
    // pointer-events:none means the chip click underneath always wins.
    expect(rule('.evidence-hovercard')).toMatch(/pointer-events:\s*none;/);
    expect(css).toContain('.evidence-hovercard--above');
    const reduced = mediaBlocks(css, '\\(prefers-reduced-motion:\\s*reduce\\)').join('\n');
    expect(reduced).toMatch(/\.evidence-hovercard\s*\{\s*animation:\s*none;/);
  });

  it('resets the UA popover box the card is shown in', () => {
    const card = rule('.evidence-hovercard');
    expect(card).toMatch(/inset:\s*auto;/);
    expect(card).toMatch(/margin:\s*0;/);
    expect(card).toMatch(/overflow:\s*visible;/);
    expect(card).toMatch(/padding:\s*var\(--sp-3\);/);
    expect(card).toMatch(/color:\s*var\(--text-1\);/);
    expect(card).toMatch(/background:\s*var\(--bg-1\);/);
  });

  it('anchors, flips and hides with its chip where CSS anchor positioning exists', () => {
    const anchored = topLevelRules(supportsBlock('\\(anchor-name:\\s*--a\\)'));
    expect(anchored.map((entry) => entry.selector)).toEqual(['.evidence-hovercard[data-anchored]']);
    const block = anchored[0]?.block ?? '';
    expect(block).toMatch(/position-area:\s*block-start;/);
    expect(block).toMatch(/position-try-fallbacks:\s*flip-block;/);
    expect(block).toMatch(/position-visibility:\s*anchors-visible;/);
    // The rect fallback's centring transforms never apply to an anchored card.
    expect(rule('.evidence-hovercard:not([data-anchored])')).toMatch(/transform:\s*translateX\(-50%\);/);
    expect(rule('.evidence-hovercard--above:not([data-anchored])')).toMatch(/transform:\s*translate\(-50%,\s*-100%\);/);
  });

  it('fades out on the instant exit tokens, and not at all under reduced motion', () => {
    expect(rule('.evidence-hovercard')).toMatch(/transition:\s*opacity var\(--dur-instant\) var\(--ease-exit\);/);
    expect(rule('.evidence-hovercard.is-closing')).toMatch(/opacity:\s*0;/);
    const reduced = mediaBlocks(css, '\\(prefers-reduced-motion:\\s*reduce\\)').join('\n');
    expect(reduced).toMatch(/\.evidence-hovercard\s*\{[^}]*transition:\s*none;/);
  });

  it('never lets its default dot fill outrank the freshness modifiers in partial 02', () => {
    // This lazy sheet lands after the partials: the default is :where(), and
    // the modifiers stay grouped in 02.
    expect(rule(':where(.evidence-hovercard__dot)')).toMatch(/background:\s*var\(--text-3\);/);
    expect(rule('.evidence-hovercard__dot')).not.toMatch(/background/);
    const partials = designCss();
    for (const bucket of ['fresh', 'aging', 'stale']) {
      expect(partials).toContain(`.evidence-hovercard__dot--${bucket}`);
    }
    expect(partials).not.toMatch(/\.evidence-hovercard\s*\{/);
  });
});
