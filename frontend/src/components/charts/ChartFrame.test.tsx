/**
 * @vitest-environment happy-dom
 *
 * ChartFrame (dataviz-07 step 2, dataviz-10): a figure whose summary is the
 * plot's description, a table twin behind a view toggle, a notice slot and
 * one persistent live region.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChartFrame } from './ChartFrame';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('ChartFrame', () => {
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

  const render = (props: Partial<Parameters<typeof ChartFrame>[0]> = {}) =>
    act(() =>
      root.render(
        <ChartFrame
          title="Opportunity Score Distribution"
          summary="89,553 borrowers scored."
          table={<table data-testid="twin"><tbody><tr><td>30-34</td></tr></tbody></table>}
          liveText="45-49: 18,800 borrowers"
          plotProps={{ tabIndex: 0, onKeyDown: vi.fn(), onFocus: vi.fn(), onBlur: vi.fn() }}
          {...props}
        >
          <svg data-testid="plot-marks" />
        </ChartFrame>,
      ),
    );

  it('renders a figure whose summary caption describes the focusable, named plot', () => {
    render();
    const figure = container.querySelector('figure.chart-frame');
    expect(figure).not.toBeNull();
    const caption = figure?.querySelector('figcaption.chart-frame__summary');
    expect(caption?.textContent).toBe('89,553 borrowers scored.');
    expect(caption?.id).toBeTruthy();
    const plot = figure?.querySelector<HTMLElement>('.chart-frame__plot');
    expect(plot?.getAttribute('aria-label')).toBe('Opportunity Score Distribution');
    expect(plot?.getAttribute('aria-describedby')).toBe(caption?.id);
    expect(plot?.tabIndex).toBe(0);
    expect(plot?.getAttribute('role')).toBe('group');
    // No heading element inside the frame: the section owns the title.
    expect(figure?.querySelector('h1, h2, h3, h4, h5, h6, .h-4')).toBeNull();
    expect(container.querySelector('[role="img"]')).toBeNull();
  });

  it('keeps one polite live region, and a notice only when given', () => {
    render();
    const live = container.querySelectorAll('[aria-live="polite"]');
    expect(live).toHaveLength(1);
    expect(live[0].textContent).toBe('45-49: 18,800 borrowers');
    expect(container.querySelector('.chart-frame__notice')).toBeNull();
    render({ notice: 'The refi-screen threshold is not built in this refresh.' });
    expect(container.querySelector('.chart-frame__notice')?.textContent).toBe(
      'The refi-screen threshold is not built in this refresh.',
    );
  });

  it('swaps the plot for the caller table and names the view it switches to, without aria-pressed', () => {
    render();
    const toggle = container.querySelector<HTMLButtonElement>('button.chart-frame__toggle');
    expect(toggle?.className).toContain('btn btn--sm');
    expect(toggle?.textContent).toBe('View as table');
    expect(toggle?.hasAttribute('aria-pressed')).toBe(false);
    expect(container.querySelector('[data-testid="twin"]')).toBeNull();

    act(() => toggle?.click());
    expect(container.querySelector('[data-testid="twin"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="plot-marks"]')).toBeNull();
    expect(toggle?.textContent).toBe('View as chart');
    expect(toggle?.hasAttribute('aria-pressed')).toBe(false);
    // The live region stays mounted but quiet while the table shows.
    expect(container.querySelector('[aria-live="polite"]')?.textContent).toBe('');

    act(() => toggle?.click());
    expect(container.querySelector('[data-testid="plot-marks"]')).not.toBeNull();
    expect(toggle?.textContent).toBe('View as table');
  });

  it('without a table node it offers no toggle and no table view (a Genie answer prints its own rows)', () => {
    render({ table: undefined });

    expect(container.querySelector('button.chart-frame__toggle')).toBeNull();
    expect(container.querySelector('.chart-frame__table')).toBeNull();
    expect(container.querySelector('[data-testid="plot-marks"]')).not.toBeNull();
    expect(container.querySelector('figcaption.chart-frame__summary')?.textContent).toBe('89,553 borrowers scored.');
    expect(container.querySelectorAll('[aria-live="polite"]')).toHaveLength(1);
  });
});
