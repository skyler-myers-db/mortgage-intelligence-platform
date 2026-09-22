/**
 * @vitest-environment happy-dom
 *
 * FilterSelect on the shared Escape stack (audit 2026-09-21 `runtime-v2`).
 * The defect lived here: the open menu bound its own `window` keydown
 * listener and never stopped the event, so the same Escape also reached the
 * floating Genie panel's listener and closed it (and, per runtime-01, killed
 * its in-flight turn). The menu is the top layer while open: one Escape
 * closes the menu ONLY, returns focus to its trigger, and nothing beneath it
 * sees that keypress.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { escapeLayerCount, pushEscapeLayer } from '../../lib/escapeStack';
import { FilterSelect } from './FilterSelect';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('FilterSelect Escape handling', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onChange = vi.fn();
  // Stands in for the floating Genie panel: a layer opened BEFORE the menu.
  const genieLayer = vi.fn();
  // Stands in for any legacy window-level Escape listener.
  const bystander = vi.fn();
  let popGenieLayer: () => void = () => undefined;

  beforeEach(() => {
    onChange.mockReset();
    genieLayer.mockReset();
    bystander.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    popGenieLayer = pushEscapeLayer(genieLayer);
    window.addEventListener('keydown', bystander);
    act(() => {
      root.render(
        <FilterSelect label="Segment" value="All" options={['All', 'In the Money']} onChange={onChange} />,
      );
    });
  });

  afterEach(() => {
    window.removeEventListener('keydown', bystander);
    act(() => root.unmount());
    container.remove();
    popGenieLayer();
    expect(escapeLayerCount()).toBe(0);
  });

  const trigger = () => {
    const el = container.querySelector<HTMLButtonElement>('button.filter');
    if (!el) throw new Error('trigger not rendered');
    return el;
  };
  const menu = () => container.querySelector('.filter-menu');
  const pressEscape = () =>
    act(() => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
      );
    });

  it('closes only the open menu and returns focus to the trigger; the layer below is untouched', () => {
    act(() => trigger().click());
    expect(menu()).not.toBeNull();
    expect(escapeLayerCount()).toBe(2);

    pressEscape();

    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(trigger());
    expect(genieLayer).not.toHaveBeenCalled();
    expect(bystander).not.toHaveBeenCalled();
    expect(onChange).not.toHaveBeenCalled();
    // The menu popped its layer on close, so the next Escape reaches Genie.
    expect(escapeLayerCount()).toBe(1);
    pressEscape();
    expect(genieLayer).toHaveBeenCalledTimes(1);
  });

  it('registers no layer while closed', () => {
    expect(menu()).toBeNull();
    expect(escapeLayerCount()).toBe(1);

    pressEscape();

    expect(genieLayer).toHaveBeenCalledTimes(1);
  });
});
