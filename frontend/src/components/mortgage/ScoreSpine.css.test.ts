/**
 * ScoreSpine.css + the --score-part-* tokens (wave 3, wow-stage-2; critic
 * fix 8 keeps these assertions here, not in the shared token tests):
 *
 *  - five categorical tokens in both themes, each >= 3:1 on --bg-1 and
 *    --bg-2 (resolved through the real tokens.css cascade);
 *  - the spine reads only those tokens, never --seg-* (segments) or
 *    --signal-* (state), and carries no [data-theme] rule or colour literal;
 *  - each legend button keeps a >= 24 x 24 hit area;
 *  - forced colours give every slice and swatch a CanvasText border on a
 *    Canvas fill (the labels carry the meaning);
 *  - the drawer's focused component card is styled here, beside the only
 *    path that sets it.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads stylesheet text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { TokenCascade, contrast, readTokensCss } from '../../test/tokenCascade';

declare const process: { cwd(): string };

const PARTS = ['economic', 'intent', 'fit', 'relationship', 'evidence'] as const;
const sheet = (): string =>
  (readFileSync(join(process.cwd(), 'src', 'components', 'mortgage', 'ScoreSpine.css'), 'utf8') as string)
    .replace(/\/\*[\s\S]*?\*\//g, '');

describe('--score-part-* tokens', () => {
  const tokens = readTokensCss();

  for (const theme of ['dark', 'light'] as const) {
    it(`each part clears 3:1 on --bg-1 and --bg-2 in the ${theme} theme`, () => {
      const cascade = new TokenCascade(tokens, { theme, accent: 'bright' });
      for (const part of PARTS) {
        const hue = cascade.color(`--score-part-${part}`);
        for (const bg of ['--bg-1', '--bg-2']) {
          expect(contrast(hue, cascade.color(bg)), `--score-part-${part} on ${bg} (${theme})`).toBeGreaterThanOrEqual(3);
        }
      }
    });
  }

  it('gives the light theme its own steps, distinct from the dark ones', () => {
    const dark = new TokenCascade(tokens, { theme: 'dark', accent: 'bright' });
    const light = new TokenCascade(tokens, { theme: 'light', accent: 'bright' });
    for (const part of PARTS) {
      expect(light.raw(`--score-part-${part}`)).not.toBe(dark.raw(`--score-part-${part}`));
    }
  });

  it('never aliases a segment or signal token', () => {
    const cascade = new TokenCascade(tokens, { theme: 'dark', accent: 'bright' });
    for (const part of PARTS) {
      expect(cascade.raw(`--score-part-${part}`)).not.toMatch(/var\(--(?:seg|signal)-/);
    }
  });
});

describe('ScoreSpine.css', () => {
  it('maps every part modifier onto its own --score-part-* token', () => {
    const css = sheet();
    for (const part of PARTS) {
      const rule = new RegExp(
        `\\.score-spine__slice--${part},\\s*\\.score-spine__seg--${part}\\s*\\{\\s*--score-spine-hue:\\s*var\\(--score-part-${part}\\);\\s*\\}`,
      );
      expect(css).toMatch(rule);
    }
  });

  it('reads no segment or signal token, no theme rule and no colour literal', () => {
    const css = sheet();
    expect(css).not.toMatch(/var\(--(?:seg|signal)-/);
    expect(css).not.toMatch(/\[data-theme/);
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(/);
  });

  it('keeps each legend button at least 24 x 24 (WCAG 2.5.8)', () => {
    const rule = /\.score-spine__seg\s*\{([^}]*)\}/.exec(sheet())?.[1] ?? '';
    expect(rule).toMatch(/min-block-size:\s*var\(--sp-6\);/);
    expect(rule).toMatch(/min-inline-size:\s*var\(--sp-6\);/);
  });

  it('draws a CanvasText border on a Canvas fill under forced colours', () => {
    const forced = /@media \(forced-colors: active\)\s*\{([\s\S]*)\}\s*$/.exec(sheet().trim())?.[1] ?? '';
    expect(forced).toMatch(/\.score-spine__slice,\s*\.score-spine__seg::before\s*\{[^}]*background-color:\s*Canvas;[^}]*border:\s*1px solid CanvasText;/);
  });

  it('styles the drawer card a spine segment focuses', () => {
    expect(sheet()).toMatch(/\.proof-component--focused\s*\{[^}]*border-color:\s*var\(--accent-ink\);/);
  });
});
