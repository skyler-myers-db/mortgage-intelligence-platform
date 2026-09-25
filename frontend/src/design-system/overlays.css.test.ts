import { describe, expect, it } from 'vitest';
import { designCss } from '../test/designCss';
import { featureStylesheets } from '../test/featureCss';

/**
 * Overlays on the top layer (2026-09-21 audit stack-05 / a11y-07 step 2 /
 * css-03 slice 2 / motion-01): every modal surface is a native <dialog>
 * opened with showModal() through hooks/useModalDialog. These pins cover the
 * CSS half: the user-agent dialog resets, `::backdrop` as the prototype
 * scrim, the entry from `display: none` (@starting-style) and the exit that
 * keeps the closed dialog rendered and in the top layer (allow-discrete).
 * Rendered proof: tests/e2e/fixture/overlays.fixture.spec.ts. New pins live
 * here so components.test.ts stays under the 900-line gate.
 */

/** Declarations only: comments cite prototype selectors and line numbers. */
const css = (): string => designCss().replace(/\/\*[\s\S]*?\*\//g, '');

/** The first top-level block whose selector list is exactly `selector`. */
function block(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = new RegExp(`(?:^|[}\\n])\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(css());
  expect(match, `${selector} is declared`).not.toBeNull();
  return match![1];
}

describe('the evidence and proof drawers are native modal dialogs', () => {
  it('resets every user-agent dialog property the prototype drawer does not set', () => {
    const drawer = block('.drawer');
    for (const declaration of [
      /position:\s*fixed;/,
      /inset:\s*0 0 0 auto;/,
      /width:\s*460px;/,
      /height:\s*100dvh;/,
      /max-inline-size:\s*100vw;/,
      /max-block-size:\s*100dvh;/,
      /margin:\s*0;/,
      /padding:\s*0;/,
      /border:\s*0;\s*border-left:\s*1px solid var\(--line-2\);/,
      /color:\s*var\(--text-1\);/,
      /background:\s*var\(--bg-1\);/,
      /overflow:\s*hidden;/,
    ]) {
      expect(drawer).toMatch(declaration);
    }
    // No z-index: the top layer paints the dialog above every tier.
    expect(drawer).not.toMatch(/z-index/);
  });

  it('never sets width more specifically than .drawer, so the proof drawers keep theirs', () => {
    const widthRules = [...css().matchAll(/([^{}]+)\{[^}]*\bwidth:/g)].map((match) => match[1].trim());
    const drawerWidthRules = widthRules.filter((selector) => /\.drawer\b/.test(selector) && selector !== '.drawer');
    expect(drawerWidthRules).toEqual([]);
    expect(block('.proof-drawer')).toMatch(/width:\s*min\(760px,\s*100vw\)/);
    expect(block('.genie-proof-drawer')).toMatch(/width:\s*560px;/);
  });

  it('draws the prototype scrim as ::backdrop, fading in and out', () => {
    expect(css()).not.toMatch(/\.drawer-scrim/);
    const backdrop = block('.drawer::backdrop');
    expect(backdrop).toMatch(/background:\s*var\(--surface-scrim\);/);
    expect(backdrop).toMatch(/backdrop-filter:\s*blur\(2px\);/);
    expect(backdrop).toMatch(/opacity:\s*0;/);
    expect(backdrop).toMatch(/display var\(--dur-exit\) allow-discrete,\s*overlay var\(--dur-exit\) allow-discrete;/);
    expect(block('.drawer[open]::backdrop')).toMatch(/opacity:\s*1;/);
  });

  it('is display:none while closed, enters from @starting-style and holds its exit in the top layer', () => {
    expect(block('.drawer:not([open])')).toMatch(/display:\s*none;/);
    expect(block('.drawer')).toMatch(/display var\(--dur-exit\) allow-discrete,\s*overlay var\(--dur-exit\) allow-discrete;/);
    expect(css()).toMatch(/@starting-style\s*\{\s*\.drawer\.is-open\s*\{\s*transform:\s*translateX\(100%\);\s*\}\s*\.drawer\[open\]::backdrop\s*\{\s*opacity:\s*0;/);
  });

  it('gives ::backdrop its own reduced-motion reset (the global one matches only *)', () => {
    expect(css()).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*\.drawer,\s*\.drawer\.is-open\s*\{\s*transition-property:\s*transform;\s*\}\s*\.drawer::backdrop,\s*\.drawer\[open\]::backdrop\s*\{\s*transition:\s*none;/,
    );
  });
});

describe('the command palette and the shortcut sheet are one full-viewport modal dialog', () => {
  it('keeps the scrim and blur on the layer itself, with a transparent ::backdrop', () => {
    const layer = block('.cmdk');
    expect(layer).toMatch(/inset:\s*0;/);
    expect(layer).toMatch(/background:\s*var\(--surface-scrim\);/);
    expect(layer).toMatch(/backdrop-filter:\s*blur\(3px\);/);
    for (const reset of [/width:\s*auto;/, /height:\s*auto;/, /max-inline-size:\s*none;/, /max-block-size:\s*none;/, /margin:\s*0;/, /border:\s*0;/, /padding-block-end:\s*0;/, /color:\s*var\(--text-1\);/, /overflow:\s*hidden;/]) {
      expect(layer).toMatch(reset);
    }
    expect(layer).not.toMatch(/z-index/);
    expect(block('.cmdk::backdrop')).toMatch(/background:\s*transparent;/);
  });

  it('fades the closed layer and slides its panel on the exit pair, instantly under reduced motion', () => {
    expect(block('.cmdk')).toMatch(/opacity var\(--dur-exit\) var\(--ease-exit\),\s*display var\(--dur-exit\) allow-discrete,\s*overlay var\(--dur-exit\) allow-discrete;/);
    expect(block('.cmdk:not([open])')).toMatch(/display:\s*none;\s*opacity:\s*0;/);
    expect(block('.cmdk__panel')).toMatch(/transition:\s*translate var\(--dur-exit\) var\(--ease-exit\);/);
    expect(block('.cmdk:not([open]) .cmdk__panel')).toMatch(/translate:\s*0 calc\(-1 \* var\(--sp-2\)\);/);
    expect(css()).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.cmdk,\s*\.cmdk__panel\s*\{\s*animation:\s*none;\s*transition:\s*none;/);
  });
});

describe('the borrower offer mock is a modal dialog with the prototype scrim as ::backdrop', () => {
  it('retires the scrim div and keeps the card centred by the dialog itself', () => {
    expect(css()).not.toMatch(/\.offer-mock-scrim/);
    const card = block('.offer-mock');
    expect(card).not.toMatch(/position:\s*relative/);
    expect(card).toMatch(/margin:\s*auto;/);
    expect(card).toMatch(/padding:\s*0;/);
    expect(card).toMatch(/color:\s*var\(--text-1\);/);
    expect(card).not.toMatch(/z-index/);
    const backdrop = block('.offer-mock::backdrop');
    expect(backdrop).toMatch(/background:\s*var\(--surface-scrim\);/);
    expect(backdrop).toMatch(/backdrop-filter:\s*blur\(2px\);/);
    expect(css()).toMatch(/@starting-style\s*\{\s*\.offer-mock\[open\]::backdrop\s*\{\s*opacity:\s*0;/);
    expect(css()).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.offer-mock::backdrop\s*\{\s*transition:\s*none;/);
  });
});

describe('the z-index tiers the top layer replaced', () => {
  const RETIRED = ['--z-drawer-scrim', '--z-drawer', '--z-palette', '--z-modal'];

  it('are consumed by no component partial and no feature stylesheet', () => {
    const sheets = [
      { file: 'design-system components', css: css() },
      ...featureStylesheets().map(({ file, css: text }) => ({ file, css: text.replace(/\/\*[\s\S]*?\*\//g, '') })),
    ];
    const consumers = sheets.flatMap(({ file, css: text }) =>
      RETIRED.filter((tier) => new RegExp(`var\\(${tier}\\)`).test(text)).map((tier) => `${file}: ${tier}`),
    );
    expect(consumers).toEqual([]);
  });
});
