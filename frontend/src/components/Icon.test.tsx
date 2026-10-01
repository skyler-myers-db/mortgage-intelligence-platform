/**
 * @vitest-environment happy-dom
 *
 * Icon strokes and glyphs (2026-09-21 audit critic-12), asserted on the
 * rendered <svg>: the prototype's fixed 1.6 stroke on a 24 unit viewBox
 * rendered 0.6-0.8 CSS px at the 9-12 px sizes most call sites use, and the
 * audit glyph was the filter's three bars.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Icon, type IconName } from './Icon';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Every glyph, as a `satisfies` table (wave-3 minor #135): adding an IconName
 * without a row here, or keeping a row for a removed name, fails `tsc`, so
 * the drawable-content check below can never silently skip a glyph.
 */
const ICON_TABLE = {
  search: true, filter: true, map: true, layers: true, bolt: true, home: true, user: true, pin: true,
  sparkle: true, chat: true, close: true, check: true, cross: true, chevdown: true, chevright: true,
  up: true, down: true, thumbup: true, thumbdown: true, info: true, shield: true, bell: true, settings: true,
  db: true, flow: true, target: true, permit: true, tag: true, building: true, doc: true, audit: true,
  link: true, play: true, send: true, tweak: true, sun: true, moon: true, money: true, equity: true,
  investor: true, export: true, account: true,
} satisfies Record<IconName, true>;
const ALL_ICONS = Object.keys(ICON_TABLE) as IconName[];

describe('Icon', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function svg(element: React.ReactElement): SVGSVGElement {
    act(() => root.render(element));
    const node = container.querySelector('svg');
    if (!node) throw new Error('no <svg> rendered');
    return node;
  }

  it.each([
    [9, '2.667'],
    [10, '2.4'],
    [11, '2.182'],
    [12, '2'],
    [14, '1.6'],
    [15, '1.6'],
    [16, '1.6'],
    [20, '1.6'],
  ])('renders at least 1 CSS px of stroke at %i px (stroke-width %s)', (size, width) => {
    const node = svg(<Icon name="filter" size={size} />);
    expect(node.getAttribute('stroke-width')).toBe(width);
    // Rendered px = stroke-width x size / 24.
    const renderedPx = (Number(width) * size) / 24;
    if (size <= 12) expect(renderedPx).toBeGreaterThanOrEqual(0.999);
  });

  it('keeps the 1.6 default at the default 16 px', () => {
    expect(svg(<Icon name="filter" />).getAttribute('stroke-width')).toBe('1.6');
  });

  it('lets an explicit strokeWidth win at any size', () => {
    expect(svg(<Icon name="filter" size={9} strokeWidth={1} />).getAttribute('stroke-width')).toBe('1');
    expect(svg(<Icon name="filter" size={16} strokeWidth="2.5" />).getAttribute('stroke-width')).toBe('2.5');
  });

  it('draws audit with its own glyph, not the filter bars', () => {
    const audit = svg(<Icon name="audit" size={12} />).innerHTML;
    const filter = svg(<Icon name="filter" size={12} />).innerHTML;
    expect(audit).not.toBe(filter);
    expect(audit).toContain('<rect');
  });

  it('draws account (the signed-in actor) apart from user (the borrower)', () => {
    const account = svg(<Icon name="account" size={15} />).innerHTML;
    const user = svg(<Icon name="user" size={15} />).innerHTML;
    expect(account).not.toBe(user);
    expect(account).toContain('<circle cx="12" cy="12" r="10"');
  });

  it.each(ALL_ICONS)('renders %s with drawable content', (name) => {
    const node = svg(<Icon name={name} />);
    expect(node.getAttribute('aria-hidden')).toBe('true');
    expect(node.getAttribute('viewBox')).toBe('0 0 24 24');
    expect(node.children.length).toBeGreaterThan(0);
  });
});
