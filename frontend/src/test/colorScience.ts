/**
 * Colour science for design-system unit tests (no browser): sRGB <-> OKLab,
 * the `color-mix(in oklab, ...)` step the CSS engine paints, OKLab distance
 * (dE x100, the dataviz validator's scale), OKLCH, and colour-vision-
 * deficiency simulation (Machado, Oliveira & Fernandes 2009, severity 1.0,
 * applied in linear RGB).
 *
 * The OKLab helpers moved here verbatim from USChoroplethMap.ramp.test.ts
 * (only `export` added) so segmentPalette.test.ts can share them.
 */
import { parseColor, type Rgba } from './tokenCascade';

export type Lab = [number, number, number];

export const toLinear = (v: number) => {
  const c = v / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};
export const fromLinear = (v: number) => {
  const c = v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
  return Math.round(Math.min(1, Math.max(0, c)) * 255);
};

export function toOklab({ r, g, b }: Rgba): Lab {
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

export function fromOklab([L, a, bb]: Lab): Rgba {
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
export function stepColor(value: string): Rgba {
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

/** OKLab of a colour already in linear RGB (0-1 channels). */
function oklabFromLinear(lr: number, lg: number, lb: number): Lab {
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

/** OKLab distance x100 (the dataviz validator's dE scale). */
export function deltaE(a: Lab, b: Lab): number {
  return 100 * Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

/** OKLCH: lightness 0-1, chroma, hue in degrees 0-360. */
export function oklch(color: Rgba): [number, number, number] {
  const [L, a, b] = toOklab(color);
  return [L, Math.hypot(a, b), ((Math.atan2(b, a) * 180) / Math.PI + 360) % 360];
}

export type CvdKind = 'protan' | 'deutan';

/** Machado et al. 2009, severity 1.0, rows applied to linear RGB. */
export const MACHADO_2009: Readonly<Record<CvdKind, readonly (readonly [number, number, number])[]>> = {
  protan: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deutan: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
};

/** OKLab of `color` as a full protanope or deuteranope sees it (clamped to the sRGB gamut). */
export function simulate(color: Rgba, kind: CvdKind): Lab {
  const linear = [toLinear(color.r), toLinear(color.g), toLinear(color.b)];
  const [r, g, b] = MACHADO_2009[kind].map((row) =>
    Math.min(1, Math.max(0, row[0] * linear[0] + row[1] * linear[1] + row[2] * linear[2])),
  );
  return oklabFromLinear(r, g, b);
}
