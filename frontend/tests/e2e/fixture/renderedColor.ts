/**
 * Colour probes for fixture specs that assert what the browser PAINTED, not
 * what the CSS text says: computed colours composited through translucent
 * backgrounds, WCAG contrast, token values as computed rgb(), and a real
 * screenshot pixel. Used by the theming and contrast specs (2026-09-21 audit
 * visual-03, css-01, css-v1, a11y-01, responsive-02).
 */
import type { Locator, Page } from '@playwright/test';

export type Rgb = [number, number, number];

export interface RenderedColors {
  /** Computed `color` composited over the background behind it. */
  fg: Rgb;
  /** The element's own background and every ancestor's, composited outward to the first opaque layer. */
  bg: Rgb;
  /** Computed `color` as the browser reports it. */
  color: string;
}

export function relativeLuminance([r, g, b]: Rgb): number {
  const channel = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(a: Rgb, b: Rgb): number {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** An opaque computed `rgb(r, g, b)` (an outline colour, a token read through asComputedRgb). */
export function parseRgb(css: string): Rgb {
  const match = /^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/.exec(css.trim());
  if (!match) throw new Error(`expected an opaque rgb() colour, got: ${css}`);
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

/**
 * Foreground and the real background behind one element, composited through
 * translucent layers (a tinted icon tile over its banner, a tab fill over
 * the drawer's tab strip) the way the browser paints them. SVG glyphs paint
 * `currentColor`, so `fg` is the glyph colour too. Fails if nothing opaque
 * is found before <html>, or on a colour the parser does not understand.
 */
export async function renderedColors(target: Locator): Promise<RenderedColors> {
  return target.evaluate((el) => {
    type Rgba = [number, number, number, number];
    const parse = (value: string): Rgba => {
      const match = /^rgba?\(([^)]+)\)$/.exec(value.trim());
      if (!match) throw new Error(`unparseable colour: ${value}`);
      const [r, g, b, a = '1'] = match[1].split(/[\s,/]+/).filter(Boolean);
      return [Number(r), Number(g), Number(b), Number(a)];
    };
    const layers: Rgba[] = [];
    let node: Element | null = el;
    while (node) {
      const layer = parse(getComputedStyle(node).backgroundColor);
      if (layer[3] > 0) layers.push(layer);
      if (layer[3] >= 1) break;
      node = node.parentElement;
    }
    if (layers.length === 0 || layers[layers.length - 1][3] < 1) {
      throw new Error(`no opaque background behind .${el.className}`);
    }
    const over = (top: Rgba, under: [number, number, number]): [number, number, number] =>
      [0, 1, 2].map((i) => Math.round(top[i] * top[3] + under[i] * (1 - top[3]))) as [number, number, number];
    let bg = layers[layers.length - 1].slice(0, 3) as [number, number, number];
    for (let i = layers.length - 2; i >= 0; i -= 1) bg = over(layers[i], bg);
    const color = getComputedStyle(el).color;
    return { fg: over(parse(color), bg), bg, color };
  });
}

/** One custom property as the element computes it (inherits theme and accent). */
export async function tokenValue(target: Locator, name: string): Promise<string> {
  return target.evaluate((el, prop) => getComputedStyle(el).getPropertyValue(prop).trim(), name);
}

/** Computed rgb() of a colour expression, so hex tokens and computed colours meet in one form. */
export async function asComputedRgb(page: Page, expression: string): Promise<string> {
  return page.evaluate((value) => {
    const probe = document.createElement('span');
    probe.style.color = value;
    document.body.appendChild(probe);
    const rgb = getComputedStyle(probe).color;
    probe.remove();
    return rgb;
  }, expression);
}

/** Colour of the centre pixel of an element, from a real screenshot decoded in-page. */
export async function centerPixel(page: Page, target: Locator): Promise<Rgb> {
  const png = await target.screenshot();
  return page.evaluate(async (base64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${base64}`;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('2d canvas unavailable');
    context.drawImage(image, 0, 0);
    const [r, g, b] = context.getImageData(Math.floor(image.width / 2), Math.floor(image.height / 2), 1, 1).data;
    return [r, g, b] as Rgb;
  }, png.toString('base64'));
}
