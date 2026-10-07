/**
 * Token-level WCAG AA gate over every user-selectable theme x accent pair.
 *
 * 2026-09-21 UI/UX audit css-01 / css-v1 / a11y-01 / responsive-02: five of
 * the eight theme x accent combinations shipped illegible tokens (light+teal
 * chip text 1.15:1, light+red chip text 1.48:1, dark+navy ink 2.06:1, the
 * light-theme focus ring 1.91:1, the red primary CTA 3.62:1). The values
 * came verbatim from design_files/index.html:164-178, whose `[data-accent]`
 * blocks outrank the light block by source order, so the light theme
 * rendered dark-tuned colours.
 *
 * This test resolves the real cascade in tokens.css (no browser, pure
 * luminance maths) and asserts the pairs the audit named. Thresholds are
 * never lowered: a pair that cannot reach AA without departing from a
 * prototype DARK value is listed in `DOCUMENTED_EXCEPTIONS` with its finding
 * id and prototype line, and the test asserts the exception list stays exact
 * (an exception that starts passing must be removed).
 */
import { describe, expect, it } from 'vitest';
import {
  TokenCascade,
  contrast,
  declaredValues,
  hex,
  over,
  readTokensCss,
  type Rgba,
} from '../test/tokenCascade';

const AA_TEXT = 4.5;
const AA_UI = 3;

const css = readTokensCss();
const THEMES = declaredValues(css, 'theme');
const ACCENTS = declaredValues(css, 'accent');

interface Pair {
  /** Finding id(s) the pair traces to. */
  finding: string;
  fg: string;
  /** Background token, optionally composited over a base token. */
  bg: string;
  base?: string;
  min: number;
}

/**
 * Pairs that stay below AA on purpose: the dark hex values are the prototype's
 * (design_files/index.html) and CLAUDE.md keeps them verbatim. Keyed by
 * `${theme}/${accent}/${fg}/${bg}`; the test fails if a listed pair passes
 * (stale exception) or an unlisted pair fails.
 */
const DOCUMENTED_EXCEPTIONS: Record<string, string> = {
  // Empty. The last entry (a11y-01, white CTA text on brand red #FF3621 at
  // 3.62:1) was retired by --accent-fill: dark + red fills with #D92D1A.
};

function textPairs(): Pair[] {
  const pairs: Pair[] = [];
  // Body text on page, surface, raised card and hover/well. --text-4 is the
  // disabled shade: WCAG 1.4.3 exempts inactive UI, so it is not gated here.
  for (const fg of ['--text-1', '--text-2', '--text-3']) {
    for (const bg of ['--bg-0', '--bg-1', '--bg-2', '--bg-3']) {
      pairs.push({ finding: 'responsive-02', fg, bg, min: AA_TEXT });
    }
  }
  // Signal colours used as text (.kpi__delta, verdicts, callouts, the
  // search-error status, Genie history/proof states, the bulk-action toast).
  // The warning hue itself is a fill (dots, bars, freshness legend) and stays
  // prototype amber; --signal-warning-ink is the text-safe variant and the
  // partials may not paint text with the fill (tokenUsage.test.ts). --bg-3
  // is the hover/well surface menus and toasts sit on.
  for (const fg of ['--signal-success', '--signal-danger', '--signal-warning-ink']) {
    for (const bg of ['--bg-0', '--bg-1', '--bg-2', '--bg-3']) {
      pairs.push({ finding: 'a11y-01', fg, bg, min: AA_TEXT });
    }
  }
  // Amber icon glyphs (.approval__ico, .degraded-banner__ico on
  // --status-warning-soft-strong; .audit__ico.amber on --status-warning-soft)
  // take the ink too: WCAG 1.4.11 non-text contrast on the tinted fill.
  for (const base of ['--bg-1', '--bg-2']) {
    for (const soft of ['--status-warning-soft', '--status-warning-soft-strong']) {
      pairs.push({ finding: 'a11y-01', fg: '--signal-warning-ink', bg: soft, base, min: AA_UI });
    }
  }
  // Active evidence-drawer tab: --accent-ink on the --accent-soft fill over
  // the drawer's --bg-2 tab strip (and --bg-1 where the same fill backs
  // active filters). It used to paint --accent (1.75:1 in light + bright).
  for (const base of ['--bg-1', '--bg-2']) {
    pairs.push({ finding: 'a11y-01', fg: '--accent-ink', bg: '--accent-soft', base, min: AA_TEXT });
  }
  // Accent ink: every accent-coloured text site (active nav, filters, links).
  for (const bg of ['--bg-0', '--bg-1', '--bg-2']) {
    pairs.push({ finding: 'css-01', fg: '--accent-ink', bg, min: AA_TEXT });
  }
  // Focus ring: WCAG 1.4.11 non-text contrast against every surface it can
  // sit on. One global :focus-visible rule consumes --focus-ring-color.
  for (const bg of ['--bg-0', '--bg-1', '--bg-2']) {
    pairs.push({ finding: 'css-v1', fg: '--focus-ring-color', bg, min: AA_UI });
  }
  // Evidence chips: text on the translucent chip fill, composited over the
  // surfaces chips sit on, and on the bare surface (chip fill may be absent).
  for (const base of ['--bg-1', '--bg-2']) {
    pairs.push({ finding: 'css-01', fg: '--chip-text', bg: '--chip-bg', base, min: AA_TEXT });
    pairs.push({ finding: 'css-01', fg: '--chip-text', bg: base, min: AA_TEXT });
  }
  // Status inks on the fills they actually sit on: `--status-*-soft` under
  // .score--high/.score--med and .audit__ico, `--status-*-soft-muted` under
  // .chip--success/warning/danger. (`--status-warning-soft-strong` carries
  // the icon glyphs gated above and the offer-mock banner.)
  for (const tone of ['success', 'warning', 'danger']) {
    const fg = `--status-${tone}-ink`;
    for (const bg of ['--bg-1', '--bg-2']) {
      pairs.push({ finding: 'responsive-02', fg, bg, min: AA_TEXT });
      for (const soft of [`--status-${tone}-soft`, `--status-${tone}-soft-muted`]) {
        pairs.push({ finding: 'responsive-02', fg, bg: soft, base: bg, min: AA_TEXT });
      }
    }
  }
  // .btn--danger keeps --signal-danger text over the subtle fill on hover.
  for (const base of ['--bg-1', '--bg-2']) {
    pairs.push({ finding: 'a11y-01', fg: '--signal-danger', bg: '--status-danger-soft-subtle', base, min: AA_TEXT });
  }
  // Primary CTA: contrast text on the CTA fill, at rest and hovered / pressed
  // (--accent-fill is the accent except dark + red, where it is #D92D1A).
  pairs.push({ finding: 'a11y-01', fg: '--accent-contrast', bg: '--accent-fill', min: AA_TEXT });
  pairs.push({ finding: 'a11y-01', fg: '--accent-contrast', bg: '--accent-fill-hover', min: AA_TEXT });
  // Skip link: the same pair (it used --bg-1 text on --accent: light + bright
  // 1.91:1, light + teal 1.57:1, dark + navy 2.06:1).
  pairs.push({ finding: 'a11y-01 skip link', fg: '--accent-contrast', bg: '--accent-fill', min: AA_TEXT });
  // Success CTA (.btn--success, the Growth Agent run card): white on the
  // prototype's dark #10B981 read 2.54:1 (deviation:success-cta-fill).
  pairs.push({ finding: 'a11y-01 success CTA', fg: '--text-on-solid', bg: '--success-fill', min: AA_TEXT });
  pairs.push({ finding: 'a11y-01 success CTA', fg: '--text-on-solid', bg: '--success-fill-hover', min: AA_TEXT });
  return pairs;
}

function background(cascade: TokenCascade, pair: Pair): Rgba {
  const bg = cascade.color(pair.bg);
  return pair.base ? over(bg, cascade.color(pair.base)) : bg;
}

describe('design token contrast matrix (tokens.css, no browser)', () => {
  it('reads the real theme and accent lists from tokens.css', () => {
    expect(THEMES.sort()).toEqual(['dark', 'light']);
    expect(ACCENTS.sort()).toEqual(['bright', 'navy', 'red', 'teal']);
  });

  it('defines the focus-ring tokens and derives the ring colour from --accent-ink by default', () => {
    const cascade = new TokenCascade(css, { theme: 'dark', accent: 'bright' });
    expect(cascade.raw('--focus-ring-color')).toBe('var(--accent-ink)');
    expect(cascade.resolve('--focus-ring-width')).toMatch(/^\d+px$/);
    expect(cascade.resolve('--focus-ring-offset')).toMatch(/^-?\d+px$/);
  });

  for (const theme of THEMES) {
    for (const accent of ACCENTS) {
      describe(`${theme} + ${accent}`, () => {
        const cascade = new TokenCascade(css, { theme, accent });
        const exceptions = new Set<string>();

        for (const pair of textPairs()) {
          const key = `${theme}/${accent}/${pair.fg}/${pair.bg}`;
          const exception = DOCUMENTED_EXCEPTIONS[key];
          const title = `${pair.fg} on ${pair.bg}${pair.base ? ` over ${pair.base}` : ''} >= ${pair.min}:1 (${pair.finding})`;
          it(exception ? `${title} — documented exception` : title, () => {
            const fg = cascade.color(pair.fg);
            const bg = background(cascade, pair);
            const ratio = contrast(fg, bg);
            const detail = `${hex(over(fg, bg))} on ${hex(bg)} = ${ratio.toFixed(2)}:1`;
            if (exception) {
              exceptions.add(key);
              expect(ratio, `stale exception, remove it: ${detail}`).toBeLessThan(pair.min);
              return;
            }
            expect(ratio, detail).toBeGreaterThanOrEqual(pair.min);
          });
        }

        it('lists every documented exception for this pair exactly once', () => {
          const expected = Object.keys(DOCUMENTED_EXCEPTIONS).filter((key) => key.startsWith(`${theme}/${accent}/`));
          expect([...exceptions].sort()).toEqual(expected.sort());
        });
      });
    }
  }
});
