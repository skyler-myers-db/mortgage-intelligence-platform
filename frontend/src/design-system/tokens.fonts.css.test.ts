/**
 * Source pins for the webfont faces at the head of tokens.css (2026-09-21
 * audit bundle-05 / css-v2). The rendered proof (one fetch per preloaded
 * woff2, the 600 < 650 < 700 < 800 weight ladder, the metric-matched
 * fallbacks) is tests/e2e/fixture/build-currency.fixture.spec.ts; this file
 * keeps the source honest between builds:
 *
 *  - exactly two webfont faces, the prototype's 'Geist' and 'Geist Mono'
 *    (design_files/index.html:42-43), each one variable woff2 (100 900) with
 *    font-display: swap, and no static @fontsource import left behind;
 *  - both metric-matched fallback families have a regular face and a real
 *    local bold face (600 900), each with all four overrides, the same line
 *    box, and the same unicode-range as their webfont, so a glyph Geist does
 *    not cover keeps the system face instead of a scaled Arial / Courier New;
 *  - --font-sans / --font-mono try the webfont, then its fallback face.
 */
import { describe, expect, it } from 'vitest';
import { readTokensCss } from '../test/tokenCascade';

interface FontFace {
  family: string;
  descriptors: Record<string, string>;
}

function fontFaces(css: string): FontFace[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  return [...text.matchAll(/@font-face\s*\{([^}]*)\}/g)].map((match) => {
    const descriptors: Record<string, string> = {};
    for (const declaration of match[1].split(';')) {
      const colon = declaration.indexOf(':');
      if (colon === -1) continue;
      descriptors[declaration.slice(0, colon).trim()] = declaration.slice(colon + 1).trim();
    }
    return { family: (descriptors['font-family'] ?? '').replace(/['"]/g, ''), descriptors };
  });
}

function customProperty(css: string, name: string): string {
  const match = new RegExp(`${name}:\\s*([^;]+);`).exec(css);
  if (!match) throw new Error(`tokens.css does not declare ${name}`);
  return match[1].trim();
}

const css = readTokensCss();
const faces = fontFaces(css);
const webfonts = faces.filter((face) => /url\(/.test(face.descriptors.src ?? ''));
const fallbacks = faces.filter((face) => /local\(/.test(face.descriptors.src ?? ''));

describe('tokens.css webfont faces', () => {
  it('declares exactly the two prototype families as webfonts', () => {
    expect(webfonts.map((face) => face.family).sort()).toEqual(['Geist', 'Geist Mono']);
  });

  it('ships each as one variable woff2 with the full weight range and swap', () => {
    for (const face of webfonts) {
      const urls = [...face.descriptors.src.matchAll(/url\(([^)]*)\)\s*format\(([^)]*)\)/g)];
      expect(urls, `${face.family} has exactly one source`).toHaveLength(1);
      expect(urls[0][1]).toMatch(/^'@fontsource-variable\/geist(?:-mono)?\/files\/geist(?:-mono)?-latin-wght-normal\.woff2'$/);
      expect(urls[0][2]).toBe("'woff2'");
      expect(face.descriptors['font-weight']).toBe('100 900');
      expect(face.descriptors['font-style']).toBe('normal');
      expect(face.descriptors['font-display']).toBe('swap');
      expect(face.descriptors['unicode-range']).toMatch(/^U\+0000-00FF,/);
    }
  });

  it('imports no static @fontsource face and names no .woff file', () => {
    expect(css).not.toMatch(/@import\s+['"]@fontsource\//);
    expect(css).not.toMatch(/\.woff['")]/);
  });
});

/** A fallback family's regular face (no weight descriptor) or its bold one (600 900). */
function fallbackFace(family: string, weight: 'regular' | 'bold'): FontFace | undefined {
  return fallbacks.find(
    (candidate) =>
      candidate.family === family &&
      (weight === 'bold' ? candidate.descriptors['font-weight'] === '600 900' : !('font-weight' in candidate.descriptors)),
  );
}

describe('tokens.css metric-matched fallback faces', () => {
  it.each([
    ['Geist Fallback', 'regular', /^local\('Arial'\), local\('Liberation Sans'\)$/],
    ['Geist Fallback', 'bold', /^local\('Arial Bold'\), local\('Liberation Sans Bold'\)$/],
    ['Geist Mono Fallback', 'regular', /^local\('Courier New'\), local\('Liberation Mono'\)$/],
    ['Geist Mono Fallback', 'bold', /^local\('Courier New Bold'\), local\('Liberation Mono Bold'\)$/],
  ] as const)('%s %s scales a local metric twin with all four overrides', (family, weight, source) => {
    const face = fallbackFace(family, weight);
    expect(face, `${family} ${weight} is declared`).toBeDefined();
    expect(face?.descriptors.src).toMatch(source);
    for (const override of ['size-adjust', 'ascent-override', 'descent-override', 'line-gap-override']) {
      expect(face?.descriptors[override], `${family} ${weight} ${override}`).toMatch(/^\d+(?:\.\d+)?%$/);
    }
  });

  it.each([
    ['Geist Fallback', 'Geist'],
    ['Geist Mono Fallback', 'Geist Mono'],
  ])('%s covers exactly the glyphs %s does, at every weight', (family, webfont) => {
    const expected = webfonts.find((candidate) => candidate.family === webfont)?.descriptors['unicode-range'];
    expect(expected).toMatch(/^U\+0000-00FF,/);
    for (const weight of ['regular', 'bold'] as const) {
      expect(fallbackFace(family, weight)?.descriptors['unicode-range'], `${family} ${weight}`).toBe(expected);
    }
  });

  it.each([['Geist Fallback'], ['Geist Mono Fallback']])('%s bold keeps the regular face line box', (family) => {
    // ascent/descent overrides are scaled by size-adjust, so the products
    // (the used ascent and descent, in em) must match across the two faces.
    const used = (face: FontFace | undefined, override: string) =>
      (parseFloat(face?.descriptors[override] ?? 'NaN') * parseFloat(face?.descriptors['size-adjust'] ?? 'NaN')) / 1e4;
    for (const override of ['ascent-override', 'descent-override']) {
      expect(used(fallbackFace(family, 'bold'), override)).toBeCloseTo(used(fallbackFace(family, 'regular'), override), 3);
    }
  });

  it('declares no other face', () => {
    expect(faces).toHaveLength(6);
  });
});

describe('font stacks', () => {
  it('try the webfont, then its metric-matched fallback, before the system faces', () => {
    expect(customProperty(css, '--font-sans')).toMatch(/^'Geist', 'Geist Fallback', /);
    expect(customProperty(css, '--font-mono')).toMatch(/^'Geist Mono', 'Geist Mono Fallback', /);
  });
});
