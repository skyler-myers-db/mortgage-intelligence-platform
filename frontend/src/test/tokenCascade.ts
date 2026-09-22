/**
 * Single responsibility: resolve `design-system/tokens.css` custom properties
 * for one theme x accent x density context WITHOUT a browser, so unit tests
 * can do WCAG luminance maths over the real token cascade.
 *
 * Only the selectors tokens.css uses as token carriers are modelled:
 * `:root`, `[data-theme="x"]`, `[data-accent="x"]`, `:root[data-density="x"]`
 * and their compounds. Element/class rules (`body`, `.caps`, `:focus-visible`)
 * carry no custom properties and are ignored, as are at-rule blocks
 * (`@media`, `@import`). Cascade order follows CSS: higher specificity wins,
 * then later source order.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// helper reads the token CSS text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';

declare const process: { cwd(): string };

export interface TokenContext {
  theme: string;
  accent: string;
  density?: string;
}

export interface Rgba {
  r: number;
  g: number;
  b: number;
  a: number;
}

interface Declaration {
  name: string;
  value: string;
  specificity: number;
  order: number;
}

interface CarrierSelector {
  specificity: number;
  matches: (ctx: TokenContext) => boolean;
}

const CARRIER_RE = /^(:root)?((?:\[data-(?:theme|accent|density)="[a-z]+"\])*)$/;
const ATTR_RE = /\[data-(theme|accent|density)="([a-z]+)"\]/g;

export const TOKENS_CSS_PATH = ['src', 'design-system', 'tokens.css'];

export function readTokensCss(): string {
  return readFileSync(join(process.cwd(), ...TOKENS_CSS_PATH), 'utf8');
}

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/** Top-level `selector { block }` pairs, skipping at-rules and statements. */
export function topLevelRules(css: string): Array<{ selector: string; block: string }> {
  const text = stripComments(css);
  const rules: Array<{ selector: string; block: string }> = [];
  let i = 0;
  while (i < text.length) {
    const open = text.indexOf('{', i);
    if (open === -1) break;
    const semicolon = text.lastIndexOf(';', open);
    const closeBefore = text.lastIndexOf('}', open);
    const start = Math.max(semicolon, closeBefore, i - 1) + 1;
    const selector = text.slice(start, open).trim();
    let depth = 1;
    let j = open + 1;
    while (j < text.length && depth > 0) {
      if (text[j] === '{') depth += 1;
      else if (text[j] === '}') depth -= 1;
      j += 1;
    }
    const block = text.slice(open + 1, j - 1);
    if (!selector.startsWith('@')) rules.push({ selector, block });
    i = j;
  }
  return rules;
}

function parseCarrier(selector: string): CarrierSelector | null {
  const match = CARRIER_RE.exec(selector.trim());
  if (!match) return null;
  const hasRoot = Boolean(match[1]);
  const attrs: Array<[keyof TokenContext, string]> = [];
  for (const attr of (match[2] ?? '').matchAll(ATTR_RE)) {
    attrs.push([attr[1] as keyof TokenContext, attr[2]]);
  }
  if (!hasRoot && attrs.length === 0) return null;
  return {
    specificity: (hasRoot ? 1 : 0) + attrs.length,
    matches: (ctx) =>
      attrs.every(([key, value]) => (ctx[key] ?? (key === 'density' ? 'comfortable' : undefined)) === value),
  };
}

function parseDeclarations(block: string): Array<{ name: string; value: string }> {
  return block
    .split(';')
    .map((part) => part.trim())
    .filter((part) => part.startsWith('--'))
    .map((part) => {
      const colon = part.indexOf(':');
      return { name: part.slice(0, colon).trim(), value: part.slice(colon + 1).trim() };
    });
}

/** The set of `[data-<attr>="x"]` values tokens.css declares for one attribute. */
export function declaredValues(css: string, attr: 'theme' | 'accent' | 'density'): string[] {
  const found = new Set<string>();
  for (const match of stripComments(css).matchAll(new RegExp(`\\[data-${attr}="([a-z]+)"\\]`, 'g'))) {
    found.add(match[1]);
  }
  return [...found];
}

export class TokenCascade {
  private readonly winners = new Map<string, Declaration>();

  constructor(css: string, private readonly ctx: TokenContext) {
    let order = 0;
    for (const rule of topLevelRules(css)) {
      const carriers = rule.selector
        .split(',')
        .map(parseCarrier)
        .filter((carrier): carrier is CarrierSelector => carrier !== null && carrier.matches(ctx));
      if (carriers.length === 0) continue;
      const specificity = Math.max(...carriers.map((carrier) => carrier.specificity));
      for (const decl of parseDeclarations(rule.block)) {
        order += 1;
        const current = this.winners.get(decl.name);
        if (!current || specificity >= current.specificity) {
          this.winners.set(decl.name, { ...decl, specificity, order });
        }
      }
    }
  }

  /** Raw declared value of a token (before `var()` substitution), or undefined. */
  raw(name: string): string | undefined {
    return this.winners.get(name)?.value;
  }

  /** Fully substituted value; throws on an undefined token so tests fail loudly. */
  resolve(name: string, seen: string[] = []): string {
    if (seen.includes(name)) throw new Error(`Circular token reference: ${[...seen, name].join(' -> ')}`);
    const value = this.raw(name);
    if (value === undefined) {
      throw new Error(`Token ${name} is not defined for ${JSON.stringify(this.ctx)}`);
    }
    return this.substitute(value, [...seen, name]);
  }

  private substitute(value: string, seen: string[]): string {
    let out = '';
    let i = 0;
    while (i < value.length) {
      const at = value.indexOf('var(', i);
      if (at === -1) {
        out += value.slice(i);
        break;
      }
      out += value.slice(i, at);
      let depth = 1;
      let j = at + 4;
      while (j < value.length && depth > 0) {
        if (value[j] === '(') depth += 1;
        else if (value[j] === ')') depth -= 1;
        j += 1;
      }
      const inner = value.slice(at + 4, j - 1);
      const comma = inner.indexOf(',');
      const ref = (comma === -1 ? inner : inner.slice(0, comma)).trim();
      const fallback = comma === -1 ? undefined : inner.slice(comma + 1).trim();
      if (this.raw(ref) !== undefined) out += this.resolve(ref, seen);
      else if (fallback !== undefined) out += this.substitute(fallback, seen);
      else throw new Error(`Token ${ref} is not defined for ${JSON.stringify(this.ctx)}`);
      i = j;
    }
    return out;
  }

  /** Resolve a token to a colour; throws when it is not a single colour value. */
  color(name: string): Rgba {
    const resolved = this.resolve(name);
    const parsed = parseColor(resolved);
    if (!parsed) throw new Error(`Token ${name} resolved to a non-colour value: ${resolved}`);
    return parsed;
  }
}

export function parseColor(input: string): Rgba | null {
  const text = input.trim();
  const hex = /^#([0-9a-f]{3,8})$/i.exec(text);
  if (hex) {
    let digits = hex[1];
    if (digits.length === 3 || digits.length === 4) {
      digits = digits.split('').map((d) => d + d).join('');
    }
    if (digits.length !== 6 && digits.length !== 8) return null;
    const n = (offset: number) => parseInt(digits.slice(offset, offset + 2), 16);
    return { r: n(0), g: n(2), b: n(4), a: digits.length === 8 ? n(6) / 255 : 1 };
  }
  const fn = /^rgba?\(\s*([^)]*)\)$/i.exec(text);
  if (fn) {
    const parts = fn[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    if (parts.length < 3 || parts.some((p) => Number.isNaN(p))) return null;
    return { r: parts[0], g: parts[1], b: parts[2], a: parts[3] ?? 1 };
  }
  return null;
}

/** Source-over compositing of a (possibly translucent) colour onto an opaque one. */
export function over(fg: Rgba, bg: Rgba): Rgba {
  const a = fg.a;
  return {
    r: fg.r * a + bg.r * (1 - a),
    g: fg.g * a + bg.g * (1 - a),
    b: fg.b * a + bg.b * (1 - a),
    a: 1,
  };
}

function channel(value: number): number {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/** WCAG 2.x relative luminance of an opaque colour. */
export function luminance(color: Rgba): number {
  return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b);
}

/** WCAG 2.x contrast ratio; `fg` may be translucent and is composited over `bg`. */
export function contrast(fg: Rgba, bg: Rgba): number {
  const solidBg = bg.a < 1 ? over(bg, { r: 255, g: 255, b: 255, a: 1 }) : bg;
  const solidFg = fg.a < 1 ? over(fg, solidBg) : fg;
  const l1 = luminance(solidFg);
  const l2 = luminance(solidBg);
  const [light, dark] = l1 >= l2 ? [l1, l2] : [l2, l1];
  return (light + 0.05) / (dark + 0.05);
}

export function hex(color: Rgba): string {
  const part = (v: number) => Math.round(v).toString(16).padStart(2, '0');
  return `#${part(color.r)}${part(color.g)}${part(color.b)}`.toUpperCase();
}
