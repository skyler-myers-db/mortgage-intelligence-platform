/**
 * The geography ramp tokens across every theme x accent pair (audit
 * dataviz-02 / responsive-05). map-encoding.fixture.spec.ts measures the
 * painted colours in the browser for the default accent in both themes; this
 * resolves the real tokens.css cascade for all eight pairs, mixes each
 * `color-mix(in oklab, ...)` step the way the CSS engine does, and pins:
 *  - adjacent steps (0..4) at least 0.06 OKLab L apart, monotone;
 *  - the top step stands >= 3:1 off the empty base (step 0), so no pair is
 *    the pale wash responsive-05 measured at 1.6:1;
 *  - the text ink of every step (ZIP tile codes, state labels) >= 4.5:1.
 */
import { describe, expect, it } from 'vitest';
import { stepColor, toOklab } from '../../test/colorScience';
import {
  TokenCascade,
  contrast,
  declaredValues,
  parseColor,
  readTokensCss,
} from '../../test/tokenCascade';

const css = readTokensCss();
const THEMES = declaredValues(css, 'theme');
const ACCENTS = declaredValues(css, 'accent');

describe('geography ramp tokens (dataviz-02 / responsive-05)', () => {
  it('reads the eight theme x accent pairs from tokens.css', () => {
    expect(THEMES.length * ACCENTS.length).toBe(8);
  });

  // D-dataviz-geo-c1: the ramp hue is the fixed per-theme data ink, so a
  // Console accent switch never repaints the choropleth.
  for (const theme of THEMES) {
    it(`${theme}: every accent resolves an identical ramp (steps 0-4 and inks 0-4)`, () => {
      const ramps = ACCENTS.map((accent) => {
        const cascade = new TokenCascade(css, { theme, accent });
        return [0, 1, 2, 3, 4].flatMap((n) => [
          stepColor(cascade.resolve(`--map-ramp-${n}`)),
          parseColor(cascade.resolve(`--map-ramp-ink-${n}`)),
        ]);
      });
      for (let i = 1; i < ramps.length; i += 1) {
        expect(ramps[i], `${ACCENTS[i]} vs ${ACCENTS[0]}`).toEqual(ramps[0]);
      }
    });
  }

  for (const theme of THEMES) {
    for (const accent of ACCENTS) {
      it(`${theme} + ${accent}: steps stay >= 0.06 OKLab L apart, the top step is >= 3:1 off the base, every step's ink reads 4.5:1`, () => {
        const cascade = new TokenCascade(css, { theme, accent });
        const steps = [0, 1, 2, 3, 4].map((n) => stepColor(cascade.resolve(`--map-ramp-${n}`)));
        const lightness = steps.map((c) => toOklab(c)[0]);
        const direction = Math.sign(lightness[4] - lightness[0]);
        for (let n = 1; n < steps.length; n += 1) {
          const delta = lightness[n] - lightness[n - 1];
          expect(Math.abs(delta), `step ${n - 1} -> ${n}: ${lightness.map((l) => l.toFixed(3)).join(' ')}`).toBeGreaterThanOrEqual(0.06);
          expect(Math.sign(delta)).toBe(direction);
        }
        expect(contrast(steps[4], steps[0]), 'top step against the empty base').toBeGreaterThanOrEqual(3);
        for (let n = 0; n < steps.length; n += 1) {
          const ink = parseColor(cascade.resolve(`--map-ramp-ink-${n}`));
          if (!ink) throw new Error(`--map-ramp-ink-${n} is not a colour`);
          expect(contrast(ink, steps[n]), `ink on step ${n}`).toBeGreaterThanOrEqual(4.5);
        }
      });
    }
  }
});
