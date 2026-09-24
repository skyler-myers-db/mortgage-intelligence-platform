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
 *  - both metric-matched fallback faces carry all four overrides;
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

describe('tokens.css metric-matched fallback faces', () => {
  it.each([
    ['Geist Fallback', /^local\('Arial'\), local\('Liberation Sans'\)$/],
    ['Geist Mono Fallback', /^local\('Courier New'\), local\('Liberation Mono'\)$/],
  ])('%s scales a local metric twin with all four overrides', (family, source) => {
    const face = fallbacks.find((candidate) => candidate.family === family);
    expect(face, `${family} is declared`).toBeDefined();
    expect(face?.descriptors.src).toMatch(source);
    for (const override of ['size-adjust', 'ascent-override', 'descent-override', 'line-gap-override']) {
      expect(face?.descriptors[override], `${family} ${override}`).toMatch(/^\d+(?:\.\d+)?%$/);
    }
  });

  it('declares no other face', () => {
    expect(faces).toHaveLength(4);
  });
});

describe('font stacks', () => {
  it('try the webfont, then its metric-matched fallback, before the system faces', () => {
    expect(customProperty(css, '--font-sans')).toMatch(/^'Geist', 'Geist Fallback', /);
    expect(customProperty(css, '--font-mono')).toMatch(/^'Geist Mono', 'Geist Mono Fallback', /);
  });
});
