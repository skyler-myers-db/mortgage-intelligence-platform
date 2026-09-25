/**
 * @vitest-environment happy-dom
 */
import { describe, expect, it, vi } from 'vitest';
import { modalLayerCount, pushModalLayer, subscribeModalLayers, topModalLayer } from './modalLayers';

describe('modalLayers', () => {
  it('reports the dialog opened last as the top, and the one below once it pops', () => {
    const drawer = document.createElement('dialog');
    const review = document.createElement('dialog');
    const listener = vi.fn();
    const unsubscribe = subscribeModalLayers(listener);

    const popDrawer = pushModalLayer(drawer);
    const popReview = pushModalLayer(review);
    expect(topModalLayer()).toBe(review);
    expect(modalLayerCount()).toBe(2);

    popReview();
    expect(topModalLayer()).toBe(drawer);
    popDrawer();
    expect(topModalLayer()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(4);
    unsubscribe();
  });

  it('pops a lower layer out of order, and ignores a second pop', () => {
    const lower = document.createElement('dialog');
    const upper = document.createElement('dialog');
    const popLower = pushModalLayer(lower);
    const popUpper = pushModalLayer(upper);
    popLower();
    popLower();
    expect(topModalLayer()).toBe(upper);
    expect(modalLayerCount()).toBe(1);
    popUpper();
    expect(modalLayerCount()).toBe(0);
  });
});
