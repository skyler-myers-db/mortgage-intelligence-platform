/**
 * @vitest-environment happy-dom
 */
/**
 * The legend's break row (audit dataviz-02 follow-up): a break that prints
 * like the one before it is printed once, while every break keeps its exact
 * value for the tooltip and for tests that read the classes back.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { buildChoroplethScale } from './USChoroplethMap.scale';
import { USChoroplethMapLegend } from './USChoroplethMapLegend';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('USChoroplethMapLegend break row', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function renderLegend(counts: number[]) {
    const scale = buildChoroplethScale(counts);
    act(() => {
      root.render(
        <USChoroplethMapLegend
          overlayOn={false}
          overlayData={null}
          overlayLoading={false}
          overlayError={null}
          totalCount={counts.reduce((sum, count) => sum + count, 0)}
          scale={scale}
          segmentCaption="marketable population"
        />,
      );
    });
    return [...document.querySelectorAll('.map-legend__break')].map((el) => ({
      text: el.textContent,
      value: Number(el.getAttribute('data-break')),
      title: el.getAttribute('title'),
    }));
  }

  it('prints a tied break once and keeps its exact value', () => {
    // max 3 under the sqrt scale: breaks 1, 1, 2 (class 2 is empty).
    const breaks = renderLegend([3, 2, 1]);
    expect(breaks.map((b) => b.value)).toEqual([1, 1, 2]);
    expect(breaks.map((b) => b.text)).toEqual(['1', '', '2']);
    expect(breaks.map((b) => b.title)).toEqual(['1', '1', '2']);
  });

  it('prints distinct breaks as compact values with the exact value in the title', () => {
    const breaks = renderLegend([21480, 17920, 14650, 11230, 9040, 6710, 4980, 3543]);
    expect(breaks.map((b) => b.text)).toEqual(['1.3K', '5.4K', '12.1K']);
    expect(breaks.map((b) => b.title)).toEqual(['1,343', '5,370', '12,083']);
  });
});
