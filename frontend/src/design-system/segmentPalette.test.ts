/**
 * The segment palette is a validated categorical palette per theme
 * (2026-09-21 audit dataviz-09; D-dataviz-geo-c2). The prototype pair
 * --seg-itm #5CE1E6 / --seg-equity #66C5FF measured OKLab dE 9.5 (the floor
 * is 15) and equity was literally the data ink; the light theme reused the
 * dark hexes on white.
 *
 * Resolved over the real token cascade (no browser). Distances are OKLab
 * dE x100; colour-vision deficiency is Machado 2009 protan and deutan at
 * severity 1.0 in linear RGB (test/colorScience); contrast is WCAG 2.x.
 *  - core six, per theme: every pair dE >= 15 normal and >= 6 under both
 *    simulations; equity >= 15 from that theme's --accent-data;
 *  - contrast >= 3:1: dark core six on --bg-1 / --bg-2; light all 13 on
 *    --bg-0, --bg-1 and --bg-2;
 *  - light, all 13: chroma >= 0.10, OKLCH L inside 0.43-0.77, and the hue
 *    family of the dark value (within 15 degrees);
 *  - dark keeps the four prototype core hexes (design_files/index.html:34-37)
 *    and re-steps only equity and retention.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the prototype text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { deltaE, oklch, simulate, toOklab } from '../test/colorScience';
import { TokenCascade, contrast, hex, readTokensCss, type Rgba } from '../test/tokenCascade';

declare const process: { cwd(): string };

const tokens = readTokensCss();
const CORE = ['itm', 'listed', 'permit', 'investor', 'equity', 'retention'] as const;
const OVERLAYS = [
  'second-lien',
  'heloc-draw',
  'equity-history',
  'refi-propensity',
  'related-itm',
  'payoff-loss',
  'permit-activity',
] as const;
const ALL = [...CORE, ...OVERLAYS];
const THEMES = ['dark', 'light'] as const;
type Theme = (typeof THEMES)[number];

const NORMAL_FLOOR = 15;
const CVD_FLOOR = 6;
const GRAPHIC_AA = 3;
const LIGHT_CHROMA_FLOOR = 0.1;
const LIGHT_L_BAND = [0.43, 0.77] as const;
const HUE_FAMILY_DEG = 15;
const SURFACES: Record<Theme, readonly string[]> = { dark: ['--bg-1', '--bg-2'], light: ['--bg-0', '--bg-1', '--bg-2'] };
const CONTRAST_SET: Record<Theme, readonly string[]> = { dark: CORE, light: ALL };

const cascade = (theme: Theme) => new TokenCascade(tokens, { theme, accent: 'bright' });
const seg = (c: TokenCascade, name: string): Rgba => c.color(`--seg-${name}`);
const pairs = <T>(items: readonly T[]): Array<[T, T]> =>
  items.flatMap((a, i) => items.slice(i + 1).map((b): [T, T] => [a, b]));
const hueGap = (a: number, b: number) => Math.abs(((b - a + 540) % 360) - 180);

describe('segment palette (dataviz-09, D-dataviz-geo-c2)', () => {
  it('covers every --seg-* token tokens.css declares', () => {
    const declared = new Set([...tokens.matchAll(/--seg-([a-z-]+):/g)].map((m) => m[1]));
    expect([...declared].sort()).toEqual([...ALL].sort());
  });

  it('dark keeps the four prototype core hexes and re-steps only equity and retention', () => {
    const prototype = readFileSync(join(process.cwd(), '..', 'design_files', 'index.html'), 'utf8') as string;
    const dark = cascade('dark');
    for (const name of CORE) {
      const original = new RegExp(`--seg-${name}:\\s*(#[0-9A-Fa-f]{6})`).exec(prototype)?.[1]?.toUpperCase();
      const current = hex(seg(dark, name));
      if (name === 'equity' || name === 'retention') expect(current, name).not.toBe(original);
      else expect(current, name).toBe(original);
    }
  });

  for (const theme of THEMES) {
    describe(theme, () => {
      const c = cascade(theme);

      it(`core pairs stay >= ${NORMAL_FLOOR} dE apart and >= ${CVD_FLOOR} under protan and deutan`, () => {
        const failures: string[] = [];
        for (const [a, b] of pairs(CORE)) {
          const [ca, cb] = [seg(c, a), seg(c, b)];
          const normal = deltaE(toOklab(ca), toOklab(cb));
          const protan = deltaE(simulate(ca, 'protan'), simulate(cb, 'protan'));
          const deutan = deltaE(simulate(ca, 'deutan'), simulate(cb, 'deutan'));
          const label = `${a} ${hex(ca)} / ${b} ${hex(cb)}: normal ${normal.toFixed(1)}, protan ${protan.toFixed(1)}, deutan ${deutan.toFixed(1)}`;
          if (normal < NORMAL_FLOOR || Math.min(protan, deutan) < CVD_FLOOR) failures.push(label);
        }
        expect(failures).toEqual([]);
      });

      it(`equity stands >= ${NORMAL_FLOOR} dE off the data ink`, () => {
        const distance = deltaE(toOklab(seg(c, 'equity')), toOklab(c.color('--accent-data')));
        expect(distance, `equity ${hex(seg(c, 'equity'))} vs --accent-data ${hex(c.color('--accent-data'))}`).toBeGreaterThanOrEqual(NORMAL_FLOOR);
      });

      it(`every gated hue clears ${GRAPHIC_AA}:1 on its surfaces`, () => {
        const failures: string[] = [];
        for (const name of CONTRAST_SET[theme]) {
          for (const surface of SURFACES[theme]) {
            const ratio = contrast(seg(c, name), c.color(surface));
            if (ratio < GRAPHIC_AA) failures.push(`${name} ${hex(seg(c, name))} on ${surface}: ${ratio.toFixed(2)}:1`);
          }
        }
        expect(failures).toEqual([]);
      });
    });
  }

  it('light hues keep chroma, the light band and the dark hue family', () => {
    const [light, dark] = [cascade('light'), cascade('dark')];
    const failures: string[] = [];
    for (const name of ALL) {
      const [L, C, h] = oklch(seg(light, name));
      const darkHue = oklch(seg(dark, name))[2];
      const label = `${name} ${hex(seg(light, name))} (L ${L.toFixed(3)}, C ${C.toFixed(3)}, h ${h.toFixed(1)} vs dark ${darkHue.toFixed(1)})`;
      if (C < LIGHT_CHROMA_FLOOR) failures.push(`${label}: chroma`);
      if (L < LIGHT_L_BAND[0] || L > LIGHT_L_BAND[1]) failures.push(`${label}: lightness band`);
      if (hueGap(h, darkHue) > HUE_FAMILY_DEG) failures.push(`${label}: hue family`);
    }
    expect(failures).toEqual([]);
  });
});
