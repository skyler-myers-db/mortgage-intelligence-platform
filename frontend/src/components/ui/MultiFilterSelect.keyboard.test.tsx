/**
 * @vitest-environment happy-dom
 *
 * MultiFilterSelect as an APG multi-select listbox (2026-09-21 audit a11y-02 /
 * stack-05). Two defects lived here: the trigger BUTTON carried
 * aria-activedescendant while DOM focus sat on a roving option, which is not
 * how the attribute works (it must be on the element that holds focus), and
 * the open menu bound its own window Escape listener, off the shared
 * topmost-layer stack, so one Escape also reached whatever was open beneath
 * it. It had no typeahead either. The Portfolio Builder keyboard contract is
 * pinned in routes/portfolio-builder.state-picker.test.tsx.
 */

import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { escapeLayerCount, pushEscapeLayer } from '../../lib/escapeStack';
import { MultiFilterSelect } from './MultiFilterSelect';
import { TYPEAHEAD_RESET_MS } from './useListboxNavigation';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const OPTIONS = [
  { value: 'AZ', label: 'Arizona' },
  { value: 'CA', label: 'California' },
  { value: 'CO', label: 'Colorado' },
  { value: 'TX', label: 'Texas' },
] as const;
type Code = (typeof OPTIONS)[number]['value'];

const changes = vi.fn<(next: Code[]) => void>();

function Harness() {
  const [value, setValue] = useState<Code[]>([]);
  return (
    <MultiFilterSelect<Code>
      label="State"
      allLabel="All states"
      selected={value}
      options={OPTIONS}
      onChange={(next) => {
        changes(next);
        setValue(next);
      }}
    />
  );
}

let container: HTMLDivElement;
let root: Root;
const genieLayer = vi.fn();
let popGenieLayer: () => void = () => undefined;

const trigger = (): HTMLButtonElement => container.querySelector<HTMLButtonElement>('button.filter') as HTMLButtonElement;
const listbox = (): HTMLElement | null => container.querySelector<HTMLElement>('[role="listbox"]');
const activeOption = (): HTMLElement | null => {
  const id = listbox()?.getAttribute('aria-activedescendant');
  return id ? document.getElementById(id) : null;
};

function press(key: string): void {
  act(() => {
    (document.activeElement ?? document.body).dispatchEvent(
      new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }),
    );
  });
}

beforeEach(() => {
  changes.mockClear();
  genieLayer.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  // Stands in for the floating Genie panel, opened before the menu.
  popGenieLayer = pushEscapeLayer(genieLayer);
  act(() => root.render(<Harness />));
  trigger().focus();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  popGenieLayer();
  expect(escapeLayerCount()).toBe(0);
});

describe('MultiFilterSelect — focus owner carries aria-activedescendant', () => {
  it('moves focus to the listbox, which names the active option; the trigger never does', () => {
    press('ArrowDown');
    expect(document.activeElement).toBe(listbox());
    expect(listbox()?.getAttribute('aria-multiselectable')).toBe('true');
    expect(activeOption()?.textContent).toBe('All states');
    expect(trigger().hasAttribute('aria-activedescendant')).toBe(false);
    expect(trigger().getAttribute('aria-controls')).toBe(listbox()?.id);
  });

  it('typeahead moves the active option; once the buffer lapses Space toggles it without closing', () => {
    vi.useFakeTimers();
    try {
      press('ArrowDown');
      press('c');
      expect(activeOption()?.textContent).toBe('California');
      press('o');
      expect(activeOption()?.textContent).toBe('Colorado');
      expect(changes).not.toHaveBeenCalled();

      act(() => {
        vi.advanceTimersByTime(TYPEAHEAD_RESET_MS + 10);
      });
      press(' ');
      expect(changes).toHaveBeenLastCalledWith(['CO']);
      expect(listbox()).not.toBeNull();
      expect(activeOption()?.textContent).toBe('Colorado');
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('MultiFilterSelect — Escape is on the shared layer stack', () => {
  it('closes only the menu and returns focus to the trigger; the layer beneath is untouched', () => {
    press('ArrowDown');
    expect(escapeLayerCount()).toBe(2);

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });

    expect(listbox()).toBeNull();
    expect(document.activeElement).toBe(trigger());
    expect(genieLayer).not.toHaveBeenCalled();
    expect(escapeLayerCount()).toBe(1);
  });
});
