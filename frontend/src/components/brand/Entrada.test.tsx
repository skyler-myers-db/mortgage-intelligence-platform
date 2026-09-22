/**
 * @vitest-environment happy-dom
 */

import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  EntradaWordmark,
  WORDMARK_INTRINSIC_HEIGHT,
  WORDMARK_INTRINSIC_WIDTH,
  wordmarkWidthForHeight,
} from './Entrada';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Audit bundle-v1: every page drew the wordmark from an unhashed /brand path
 * (no Cache-Control) with a `height` only, so the header shifted when the
 * bytes decoded. The <img> must now come from the Vite asset pipeline and
 * reserve its box up front.
 */

describe('EntradaWordmark', () => {
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

  function render(node: ReactNode): HTMLImageElement {
    act(() => root.render(node));
    const img = container.querySelector<HTMLImageElement>('img.entrada-wordmark-img');
    if (!img) throw new Error('wordmark <img> not rendered');
    return img;
  }

  it('is served from the hashed Vite asset pipeline, not the /brand mount', () => {
    const img = render(<EntradaWordmark height={16} />);
    const src = img.getAttribute('src') ?? '';

    expect(src).not.toMatch(/^\/brand\//);
    expect(src).toMatch(/entrada-wordmark.*\.webp$/);
    expect(img.getAttribute('alt')).toBe('Entrada');
  });

  it.each([16, 28, 44])('reserves an explicit %ipx-high box at the 2048:214 ratio', (height) => {
    const img = render(<EntradaWordmark height={height} />);
    const expectedWidth = Math.round(height * (WORDMARK_INTRINSIC_WIDTH / WORDMARK_INTRINSIC_HEIGHT));

    expect(img.getAttribute('height')).toBe(String(height));
    expect(img.getAttribute('width')).toBe(String(expectedWidth));
    expect(wordmarkWidthForHeight(height)).toBe(expectedWidth);
  });

  it('honours the fontSize back-compat alias and the 28px default', () => {
    expect(render(<EntradaWordmark fontSize={20} />).getAttribute('width')).toBe(String(wordmarkWidthForHeight(20)));
    expect(render(<EntradaWordmark />).getAttribute('height')).toBe('28');
  });

  it('pins the master ratio the widths are derived from', () => {
    expect(WORDMARK_INTRINSIC_WIDTH).toBe(2048);
    expect(WORDMARK_INTRINSIC_HEIGHT).toBe(214);
    expect(wordmarkWidthForHeight(214)).toBe(2048);
  });
});
