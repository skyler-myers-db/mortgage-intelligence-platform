import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the design-system CSS text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { designCss } from '../test/designCss';

declare const process: { cwd(): string };

/**
 * Follow-ups due CSS (D-shell-deviations-a; deviation:followups-rail-item,
 * deviation:followups-tray). CSS pins live here, never in components.test.ts.
 */

const read = (...parts: string[]): string => readFileSync(join(process.cwd(), 'src', ...parts), 'utf8');
/** Declarations only: comments cite prototype lines and pixel sizes. */
const strip = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, '');

const appShell = () => strip(read('design-system', 'components', '01-app-shell.css'));
const motion = () => strip(read('design-system', 'components', '12-motion-and-viewport.css'));
const tray = () => strip(read('components', 'layout', 'FollowUpsTray.css'));

function appShellBlocks(css: string): string[] {
  return [...css.matchAll(/\.app-shell\s*\{([^}]*)\}/g)].map((match) => match[1]);
}

describe('the rail width token', () => {
  it('is declared on .app-shell at 72 px and restated at each breakpoint', () => {
    const declared = [...appShellBlocks(appShell()), ...appShellBlocks(motion())]
      .map((block) => /--shell-rail-w:\s*([^;]+);/.exec(block)?.[1])
      .filter((value): value is string => value !== undefined);
    expect(declared).toEqual(['72px', '56px', '80px', '96px']);
  });

  it('drives every .app-shell grid-template-columns, so the computed grid is unchanged', () => {
    const columns = [...appShellBlocks(appShell()), ...appShellBlocks(motion())]
      .map((block) => /grid-template-columns:\s*([^;]+);/.exec(block)?.[1])
      .filter((value): value is string => value !== undefined);
    expect(columns.length).toBe(4);
    for (const value of columns) expect(value).toBe('var(--shell-rail-w) 1fr');
  });

  it('places the tray beside it', () => {
    expect(tray()).toMatch(/inset-inline-start:\s*calc\(var\(--shell-rail-w\) \+ var\(--sp-2\)\);/);
  });
});

describe('the due dot', () => {
  it('is a static dot on tokens, inside the item, never animated or clickable', () => {
    const rule = /\.rail__item\.has-due::after\s*\{([^}]*)\}/.exec(appShell())?.[1] ?? '';
    expect(rule).toContain('background: var(--signal-warning);');
    expect(rule).toContain('inline-size: var(--sp-3);');
    expect(rule).toContain('block-size: var(--sp-3);');
    expect(rule).toContain('border-radius: var(--r-pill);');
    expect(rule).toContain('box-shadow: 0 0 0 calc(var(--sp-1) / 2) var(--bg-1);');
    expect(rule).toContain('inset-block-start: var(--sp-2);');
    expect(rule).toContain('inset-inline-end: var(--sp-2);');
    expect(rule).toContain('pointer-events: none;');
    expect(rule).not.toMatch(/animation|#[0-9a-f]{3,8}\b|\d+px/i);
  });

  it('paints CanvasText under forced colours', () => {
    expect(appShell()).toMatch(
      /@media \(forced-colors: active\)\s*\{\s*\.rail__item\.has-due::after\s*\{[^}]*background:\s*CanvasText;/,
    );
  });

  it('ships in the shell cascade', () => {
    expect(designCss()).toContain('.rail__item.has-due::after');
  });
});

describe('the tray row grammar', () => {
  it('gives the meta line the full row, so the due time always starts it', () => {
    expect(tray()).toMatch(/\.followups__meta\s*\{[^}]*flex-basis:\s*100%;/);
  });

  it('keeps each reason qualifier whole without letting it overflow', () => {
    const rule = /\.followups__qualifier\s*\{([^}]*)\}/.exec(tray())?.[1] ?? '';
    expect(rule).toContain('display: inline-block;');
    expect(rule).toContain('max-inline-size: 100%;');
    expect(rule).not.toContain('nowrap');
  });
});

describe('the tray layer', () => {
  it('declares --z-tray in tokens.css above the Console and Genie', () => {
    const tokens = read('design-system', 'tokens.css');
    expect(tokens).toMatch(/:root\s*\{\s*--z-tray:\s*55;\s*\}/);
    expect(tray()).toContain('z-index: var(--z-tray);');
  });

  it('resets the UA popover box and scrolls its body without chaining', () => {
    const css = tray();
    expect(css).toMatch(/\.followups\s*\{[^}]*inset:\s*auto;[^}]*margin:\s*0;/);
    expect(css).toMatch(/\.followups__body\s*\{[^}]*overscroll-behavior:\s*contain;/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.followups\s*\{\s*transition:\s*none;/);
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(/);
  });
});
