/**
 * The geography ramp tokens across every theme x accent pair (audit
 * dataviz-02 / responsive-05). map-encoding.fixture.spec.ts measures the
 * painted colours in the browser for the default accent in both themes; this
 * resolves the real tokens.css cascade for all eight pairs, mixes each
 * `color-mix(in oklab, ...)` step the way the CSS engine does, and pins:
 *  - adjacent steps (0..4) at least 0.06 OKLab L apart, monotone;
 *  - the text ink of every step (ZIP tile codes, state labels) >= 4.5:1.
 */
import { describe, expect, it } from 'vitest';
import {
  TokenCascade,
  contrast,
  declaredValues,
  parseColor,
  readTokensCss,
  type Rgba,
} from '../../test/tokenCascade';

const css = readTokensCss();
const THEMES = declaredValues(css, 'theme');
const ACCENTS = declaredValues(css, 'accent');

type Lab = [number, number, number];

const toLinear = (v: number) => {
  const c = v / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};
const fromLinear = (v: number) => {
  const c = v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
  return Math.round(Math.min(1, Math.max(0, c)) * 255);
};

function toOklab({ r, g, b }: Rgba): Lab {
  const [lr, lg, lb] = [toLinear(r), toLinear(g), toLinear(b)];
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

function fromOklab([L, a, bb]: Lab): Rgba {
  const l = (L + 0.3963377774 * a + 0.2158037573 * bb) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * bb) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * bb) ** 3;
  return {
    r: fromLinear(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    g: fromLinear(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    b: fromLinear(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
    a: 1,
  };
}

/** A resolved step: a plain colour, or `color-mix(in oklab, A p%, B)` of two plain colours. */
function stepColor(value: string): Rgba {
  const mix = /^color-mix\(in oklab,\s*(\S+)\s+(\d+(?:\.\d+)?)%,\s*(\S+)\)$/.exec(value);
  if (!mix) {
    const plain = parseColor(value);
    if (!plain) throw new Error(`unparseable ramp step: ${value}`);
    return plain;
  }
  const [a, b] = [parseColor(mix[1]), parseColor(mix[3])];
  if (!a || !b) throw new Error(`unparseable colour in ${value}`);
  const p = Number(mix[2]) / 100;
  const [la, lb] = [toOklab(a), toOklab(b)];
  return fromOklab([0, 1, 2].map((i) => la[i] * p + lb[i] * (1 - p)) as Lab);
}

describe('geography ramp tokens (dataviz-02 / responsive-05)', () => {
  it('reads the eight theme x accent pairs from tokens.css', () => {
    expect(THEMES.length * ACCENTS.length).toBe(8);
  });

  for (const theme of THEMES) {
    for (const accent of ACCENTS) {
      it(`${theme} + ${accent}: equal-looking steps stay >= 0.06 OKLab L apart and every step's ink reads 4.5:1`, () => {
        const cascade = new TokenCascade(css, { theme, accent });
        const steps = [0, 1, 2, 3, 4].map((n) => stepColor(cascade.resolve(`--map-ramp-${n}`)));
        const lightness = steps.map((c) => toOklab(c)[0]);
        const direction = Math.sign(lightness[4] - lightness[0]);
        for (let n = 1; n < steps.length; n += 1) {
          const delta = lightness[n] - lightness[n - 1];
          expect(Math.abs(delta), `step ${n - 1} -> ${n}: ${lightness.map((l) => l.toFixed(3)).join(' ')}`).toBeGreaterThanOrEqual(0.06);
          expect(Math.sign(delta)).toBe(direction);
        }
        for (let n = 0; n < steps.length; n += 1) {
          const ink = parseColor(cascade.resolve(`--map-ramp-ink-${n}`));
          if (!ink) throw new Error(`--map-ramp-ink-${n} is not a colour`);
          expect(contrast(ink, steps[n]), `ink on step ${n}`).toBeGreaterThanOrEqual(4.5);
        }
      });
    }
  }
});
