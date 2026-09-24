/**
 * Real-pixel ink probe for the forced-colors proofs (2026-09-21 audit
 * responsive-v3 / a11y-10). Computed colours cannot see what Chromium paints
 * in forced-colors mode: it draws a Canvas "readability backplate" behind
 * every text run whose forced-color-adjust is auto, whatever the element's
 * own background is. So a label inked HighlightText inside a Highlight fill
 * computes to a perfect pair and paints as a solid Canvas box. This module
 * reads the screenshot instead:
 *
 *  - a text run is sampled over its own Range client rect (not the element
 *    box, which also holds the surrounding fill and would pass while the
 *    words are invisible), inset by one pixel so a snapped edge row cannot
 *    stand in for the ink, and pixels of the state's own fill never count
 *    as ink;
 *  - an svg glyph (no backplate) is sampled over its own box.
 *
 * In each sample the dominant colour is the ground the ink sits on. The ink
 * is what the most contrasting INK_SHARE of the sample's pixels paints: the
 * reported ratio is the contrast that at least that share of pixels reaches
 * against the ground. A sample with no ink at all reports 1:1.
 *
 * Read the page at a devicePixelRatio of SAMPLE_SCALE or more (the spec's
 * `test.use({ deviceScaleFactor })`): at 1x a hairline icon (the 9px remove
 * cross strokes 0.6 CSS px) has no pixel at its ink colour, only
 * anti-aliased blends of ink and ground, so its measured contrast would be
 * the stroke's coverage, not its colour.
 */
import type { Locator, Page } from '@playwright/test';
import { asComputedRgb, type Rgb } from './renderedColor';

/** Share of a sample that must reach the reported contrast to count as painted ink. */
export const INK_SHARE = 0.03;

/** The devicePixelRatio a pixel-ink spec renders at, so a hairline glyph has a fully covered core. */
export const SAMPLE_SCALE = 3;

export interface PaintedInk {
  kind: 'text' | 'glyph';
  /** The sampled run's words, or the svg's nearest classed owner. */
  what: string;
  /** The sample's dominant colour: the backplate or fill behind the ink. */
  ground: Rgb;
  /** The pixel colour at the INK_SHARE contrast rank, or null when the sample is all ground. */
  ink: Rgb | null;
  /** Share of the sample painted in any colour other than the ground. */
  share: number;
  /** Contrast against the ground that at least INK_SHARE of the pixels reach; 1 when none. */
  ratio: number;
}

interface Sample {
  kind: 'text' | 'glyph';
  what: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Every visible text run and svg glyph inside `target` (and `target` itself),
 * sampled from one real viewport screenshot. `fill` is the state's painted
 * fill: inside a text run's rect it is surround, never ink. Waits out every
 * transition in the subtree first (the reduced-motion reset gives each
 * element a 0.01ms `all` transition). Fails if the target has nothing to
 * sample.
 */
export async function paintedInks(page: Page, target: Locator, fill: Rgb | null = null): Promise<PaintedInk[]> {
  await target.scrollIntoViewIfNeeded();
  const samples = await target.evaluate(async (root): Promise<Sample[]> => {
    await Promise.all(
      root
        .getAnimations({ subtree: true })
        .filter((animation) => animation instanceof CSSTransition)
        .map((transition) => transition.finished.catch(() => undefined)),
    );
    const shown = (el: Element) => el.checkVisibility({ opacityProperty: true, visibilityProperty: true });
    /** The rect clipped by the viewport and every overflow-clipping ancestor up to root. */
    const clip = (rect: DOMRect, from: Element) => {
      let [left, top, right, bottom] = [rect.left, rect.top, rect.right, rect.bottom];
      for (let node: Element | null = from; node; node = node === root ? null : node.parentElement) {
        const style = getComputedStyle(node);
        if (style.overflowX === 'visible' && style.overflowY === 'visible') continue;
        const box = node.getBoundingClientRect();
        [left, top, right, bottom] = [Math.max(left, box.left), Math.max(top, box.top), Math.min(right, box.right), Math.min(bottom, box.bottom)];
      }
      [left, top] = [Math.max(left, 0), Math.max(top, 0)];
      [right, bottom] = [Math.min(right, innerWidth), Math.min(bottom, innerHeight)];
      return { left, top, right, bottom };
    };
    const out: Sample[] = [];
    const push = (kind: Sample['kind'], what: string, rect: DOMRect, from: Element, inset: number) => {
      const box = clip(rect, from);
      const x = Math.ceil(box.left) + inset;
      const y = Math.ceil(box.top) + inset;
      const width = Math.floor(box.right) - inset - x;
      const height = Math.floor(box.bottom) - inset - y;
      if (width >= 2 && height >= 2) out.push({ kind, what, x, y, width, height });
    };
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const words = node.textContent?.trim() ?? '';
      const host = node.parentElement;
      if (!words || !host || !shown(host) || host.closest('svg')) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const what = `${host.getAttribute('class') || host.tagName.toLowerCase()} "${words}"`;
      for (const rect of range.getClientRects()) push('text', what, rect, host, 1);
    }
    const svgs = root instanceof SVGSVGElement ? [root] : [...root.querySelectorAll('svg')];
    for (const svg of svgs) {
      if (!shown(svg) || svg.parentElement?.closest('svg')) continue;
      const owner = svg.closest('[class]:not([class=""])')?.getAttribute('class') ?? 'svg';
      push('glyph', owner, svg.getBoundingClientRect(), svg, 0);
    }
    return out;
  });
  if (samples.length === 0) throw new Error('paintedInks: nothing visible to sample');
  // One device-scale screenshot of the samples' union (viewport coordinates).
  const x = Math.min(...samples.map((sample) => sample.x));
  const y = Math.min(...samples.map((sample) => sample.y));
  const clip = {
    x,
    y,
    width: Math.max(...samples.map((sample) => sample.x + sample.width)) - x,
    height: Math.max(...samples.map((sample) => sample.y + sample.height)) - y,
  };
  const png = await page.screenshot({ animations: 'disabled', caret: 'hide', scale: 'device', clip });
  return page.evaluate(
    async ({ base64, regions, origin, share, minScale, surround }) => {
      const image = new Image();
      image.src = `data:image/png;base64,${base64}`;
      await image.decode();
      const scale = image.width / origin.width;
      if (scale < minScale - 0.01) throw new Error(`sampled at ${scale}x; render at deviceScaleFactor ${minScale} or more`);
      const canvas = document.createElement('canvas');
      canvas.width = image.width;
      canvas.height = image.height;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      if (!context) throw new Error('2d canvas unavailable');
      context.drawImage(image, 0, 0);
      type Triple = [number, number, number];
      const channel = (v: number) => {
        const c = v / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      };
      const luminance = ([r, g, b]: Triple) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
      const ratio = (a: Triple, b: Triple) => {
        const [hi, lo] = [luminance(a), luminance(b)].sort((p, q) => q - p);
        return (hi + 0.05) / (lo + 0.05);
      };
      const near = (a: Triple, b: Triple) => a.every((value, i) => Math.abs(value - b[i]) <= 2);
      const rgb = (key: number): Triple => [(key >> 16) & 255, (key >> 8) & 255, key & 255];
      return regions.map((region) => {
        const [left, top] = [Math.round((region.x - origin.x) * scale), Math.round((region.y - origin.y) * scale)];
        const [width, height] = [Math.round(region.width * scale), Math.round(region.height * scale)];
        const { data } = context.getImageData(left, top, width, height);
        const counts = new Map<number, number>();
        for (let i = 0; i < data.length; i += 4) {
          const key = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
          counts.set(key, (counts.get(key) ?? 0) + 1);
        }
        const total = width * height;
        const groundKey = [...counts].sort((a, b) => b[1] - a[1])[0][0];
        const ground = rgb(groundKey);
        // Every other colour, most contrasting first; the state fill inside a
        // text rect is surround, and a rounding twin of the ground is ground.
        const inks = [...counts]
          .map(([key, count]) => ({ color: rgb(key), count }))
          .filter(({ color }) => !near(color, ground) && !(region.kind === 'text' && surround && near(color, surround)))
          .map((entry) => ({ ...entry, contrast: ratio(entry.color, ground) }))
          .sort((a, b) => b.contrast - a.contrast);
        const needed = Math.ceil(share * total);
        let covered = 0;
        const reached = inks.find((entry) => (covered += entry.count) >= needed) ?? null;
        return {
          kind: region.kind,
          what: region.what,
          ground,
          ink: reached ? reached.color : null,
          share: inks.reduce((sum, entry) => sum + entry.count, 0) / total,
          ratio: reached ? reached.contrast : 1,
        };
      });
    },
    { base64: png.toString('base64'), regions: samples, origin: clip, share: INK_SHARE, minScale: SAMPLE_SCALE, surround: fill },
  );
}

/**
 * The painted colours of an element's inset-ring band (its first `band` CSS
 * px from the inline-start edge) and of its fill just inside that band, each
 * the dominant colour over the middle half of the element's height, so
 * rounded corners and the text backplate stay out of both reads.
 */
export async function insetRingBand(page: Page, target: Locator, band: number): Promise<{ ring: Rgb; inside: Rgb }> {
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  if (!box) throw new Error('insetRingBand: target is not rendered');
  const clip = { x: Math.ceil(box.x), y: Math.ceil(box.y + box.height / 4), width: 2 * band + 1, height: Math.floor(box.height / 2) };
  const png = await page.screenshot({ animations: 'disabled', caret: 'hide', scale: 'device', clip });
  return page.evaluate(
    async ({ base64, cssWidth, band: ringWidth }) => {
      const image = new Image();
      image.src = `data:image/png;base64,${base64}`;
      await image.decode();
      const canvas = document.createElement('canvas');
      [canvas.width, canvas.height] = [image.width, image.height];
      const context = canvas.getContext('2d', { willReadFrequently: true });
      if (!context) throw new Error('2d canvas unavailable');
      context.drawImage(image, 0, 0);
      const scale = image.width / cssWidth;
      const dominant = (fromCss: number, toCss: number): [number, number, number] => {
        const left = Math.round(fromCss * scale);
        const { data } = context.getImageData(left, 0, Math.max(1, Math.round(toCss * scale) - left), image.height);
        const counts = new Map<number, number>();
        for (let i = 0; i < data.length; i += 4) {
          const key = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
          counts.set(key, (counts.get(key) ?? 0) + 1);
        }
        const key = [...counts].sort((a, b) => b[1] - a[1])[0][0];
        return [(key >> 16) & 255, (key >> 8) & 255, key & 255];
      };
      return { ring: dominant(0, ringWidth), inside: dominant(ringWidth + 1, 2 * ringWidth + 1) };
    },
    { base64: png.toString('base64'), cssWidth: clip.width, band },
  );
}

/**
 * The colour a system-colour fill paints over the forced Canvas behind it.
 * Chromium's forced palettes give Highlight an alpha (0.8), so the painted
 * fill is the composite, not the keyword's rgb.
 */
export async function paintedSystemFill(page: Page, keyword: string): Promise<Rgb> {
  const parse = (css: string) => {
    const match = /^rgba?\(([^)]+)\)$/.exec(css.trim());
    if (!match) throw new Error(`unparseable colour: ${css}`);
    const [r, g, b, a = '1'] = match[1].split(/[\s,/]+/).filter(Boolean);
    return [Number(r), Number(g), Number(b), Number(a)];
  };
  const top = parse(await asComputedRgb(page, keyword));
  const under = parse(await asComputedRgb(page, 'Canvas'));
  if (under[3] !== 1) throw new Error('forced Canvas is expected to be opaque');
  return [0, 1, 2].map((i) => Math.round(top[i] * top[3] + under[i] * (1 - top[3]))) as Rgb;
}

/** Channel-wise equality within `tolerance` (screenshot rounding). */
export function sameColor(a: Rgb, b: Rgb, tolerance = 2): boolean {
  return a.every((value, i) => Math.abs(value - b[i]) <= tolerance);
}

/** One sample as a failure message line. */
export function describeInk(state: string, ink: PaintedInk): string {
  const color = (value: Rgb | null) => (value ? `rgb(${value.join(', ')})` : 'none');
  return `${state}: ${ink.kind} ${ink.what}: ink ${color(ink.ink)} on ${color(ink.ground)} at ${ink.ratio.toFixed(2)}:1 (${(ink.share * 100).toFixed(1)}% non-ground)`;
}
