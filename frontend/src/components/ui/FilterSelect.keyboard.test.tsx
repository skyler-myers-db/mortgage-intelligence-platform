/**
 * @vitest-environment happy-dom
 *
 * FilterSelect as an APG select-only combobox (2026-09-21 audit a11y-02 /
 * tables-06). The defects lived in this component: the trigger was a plain
 * button, the options had no ids and nothing carried aria-activedescendant,
 * so arrowing through the 31 filter menus was silent to screen readers;
 * there was no Home/End or typeahead, Space closed without selecting, Tab and
 * blur left the menu open, and the menu could only open downward. Every test
 * drives the rendered component with the keyboard.
 */

import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FilterSelect } from './FilterSelect';
import { TYPEAHEAD_RESET_MS } from './useListboxNavigation';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const STATES = ['All states', 'IL', 'TX', 'CA', 'FL', 'AZ', 'WA', 'CO', 'GA'];
const changes = vi.fn<(next: string) => void>();

function Harness({ options = STATES, initial = STATES[0] }: { options?: string[]; initial?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <FilterSelect
        label="STATE"
        value={value}
        options={options}
        onChange={(next) => {
          changes(next);
          setValue(next);
        }}
      />
      <button type="button" id="after">Next control</button>
    </>
  );
}

let container: HTMLDivElement;
let root: Root;

const combobox = (): HTMLButtonElement => {
  const el = container.querySelector<HTMLButtonElement>('button.filter');
  if (!el) throw new Error('combobox not rendered');
  return el;
};
const listbox = (): HTMLElement | null => container.querySelector<HTMLElement>('[role="listbox"]');
const options = (): HTMLElement[] => [...container.querySelectorAll<HTMLElement>('[role="option"]')];
const activeOption = (): HTMLElement | null => {
  const id = combobox().getAttribute('aria-activedescendant');
  return id ? document.getElementById(id) : null;
};

function press(key: string): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  act(() => {
    (document.activeElement ?? document.body).dispatchEvent(event);
  });
  return event;
}

function type(text: string): void {
  for (const char of text) press(char);
}

function render(props: { options?: string[]; initial?: string } = {}): void {
  act(() => root.render(<Harness {...props} />));
  combobox().focus();
}

beforeEach(() => {
  changes.mockClear();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  vi.useRealTimers();
  act(() => root.unmount());
  container.remove();
});

describe('FilterSelect — select-only combobox semantics', () => {
  it('keeps focus on a role=combobox trigger whose aria-activedescendant names a real option', () => {
    render({ initial: 'TX' });
    expect(combobox().getAttribute('role')).toBe('combobox');
    expect(combobox().getAttribute('aria-expanded')).toBe('false');
    expect(combobox().hasAttribute('aria-activedescendant')).toBe(false);

    press('ArrowDown');

    expect(document.activeElement).toBe(combobox());
    expect(combobox().getAttribute('aria-expanded')).toBe('true');
    expect(combobox().getAttribute('aria-controls')).toBe(listbox()?.id);
    expect(options().every((option) => option.id.length > 0)).toBe(true);
    // Opens on the current value, and the id resolves to that option.
    expect(activeOption()).toBe(options()[2]);
    expect(activeOption()?.getAttribute('aria-selected')).toBe('true');
  });

  it('moves the active option with wrapping arrows, Home and End', () => {
    render();
    press('ArrowDown');
    expect(activeOption()?.textContent).toBe('All states');
    press('ArrowUp');
    expect(activeOption()?.textContent).toBe('GA');
    press('ArrowDown');
    expect(activeOption()?.textContent).toBe('All states');
    press('End');
    expect(activeOption()?.textContent).toBe('GA');
    press('Home');
    expect(activeOption()?.textContent).toBe('All states');
    expect(changes).not.toHaveBeenCalled();
  });

  it('Space and Enter select the active option, close, and keep focus on the combobox', () => {
    render();
    press(' ');
    expect(listbox()).not.toBeNull();
    press('ArrowDown');
    press(' ');
    expect(changes).toHaveBeenLastCalledWith('IL');
    expect(listbox()).toBeNull();
    expect(document.activeElement).toBe(combobox());

    press('Enter');
    press('End');
    press('Enter');
    expect(changes).toHaveBeenLastCalledWith('GA');
    expect(combobox().getAttribute('aria-label')).toBe('STATE: GA');
  });
});

describe('FilterSelect — typeahead', () => {
  it('a typed letter opens the closed combobox on the first matching option', () => {
    render();
    type('c');
    expect(listbox()).not.toBeNull();
    expect(activeOption()?.textContent).toBe('CA');
    expect(changes).not.toHaveBeenCalled();
  });

  it('letters typed within the buffer window refine the match, and Enter selects it', () => {
    render();
    type('co');
    expect(activeOption()?.textContent).toBe('CO');
    press('Enter');
    expect(changes).toHaveBeenLastCalledWith('CO');
  });

  it('a repeated letter cycles through the options that start with it', () => {
    render();
    type('c');
    expect(activeOption()?.textContent).toBe('CA');
    type('c');
    expect(activeOption()?.textContent).toBe('CO');
    type('c');
    expect(activeOption()?.textContent).toBe('CA');
  });

  it(`the buffer resets after ${TYPEAHEAD_RESET_MS} ms`, () => {
    vi.useFakeTimers();
    render();
    type('a');
    expect(activeOption()?.textContent).toBe('All states');
    act(() => {
      vi.advanceTimersByTime(TYPEAHEAD_RESET_MS + 10);
    });
    // A fresh one-letter search, not "aw": it cycles on from the active option.
    type('w');
    expect(activeOption()?.textContent).toBe('WA');
    type('a');
    // "wa" within the window still refines to WA; a lone "a" would cycle to All states.
    expect(activeOption()?.textContent).toBe('WA');
  });

  it('Space extends a running search instead of selecting', () => {
    render({ options: ['All', 'In escrow', 'In the Money'], initial: 'All' });
    type('in');
    expect(activeOption()?.textContent).toBe('In escrow');
    type(' t');
    expect(changes).not.toHaveBeenCalled();
    expect(activeOption()?.textContent).toBe('In the Money');
  });
});

describe('FilterSelect — dismissal', () => {
  it('Tab closes the menu without swallowing the key', () => {
    render();
    press('ArrowDown');
    expect(listbox()).not.toBeNull();
    const tab = press('Tab');
    expect(listbox()).toBeNull();
    expect(tab.defaultPrevented).toBe(false);
    expect(changes).not.toHaveBeenCalled();
  });

  it('focus leaving the filter closes the menu', () => {
    render();
    press('ArrowDown');
    expect(listbox()).not.toBeNull();
    act(() => document.getElementById('after')?.focus());
    expect(listbox()).toBeNull();
    expect(combobox().getAttribute('aria-expanded')).toBe('false');
  });
});

describe('FilterSelect — viewport-edge flip', () => {
  const restore: Array<() => void> = [];

  function stubLayout(triggerTop: number, viewportHeight: number, menuHeight: number): void {
    const innerHeight = Object.getOwnPropertyDescriptor(window, 'innerHeight');
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: viewportHeight });
    restore.push(() => {
      if (innerHeight) Object.defineProperty(window, 'innerHeight', innerHeight);
    });
    const rect = vi
      .spyOn(HTMLButtonElement.prototype, 'getBoundingClientRect')
      .mockReturnValue(new DOMRect(0, triggerTop, 120, 28));
    restore.push(() => rect.mockRestore());
    const offsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains('filter-menu') ? menuHeight : 0;
      },
    });
    restore.push(() => {
      if (offsetHeight) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', offsetHeight);
    });
  }

  afterEach(() => {
    while (restore.length > 0) restore.pop()?.();
  });

  it('opens upward when the menu does not fit below the viewport edge', () => {
    stubLayout(700, 800, 280);
    render();
    press('ArrowDown');
    expect(listbox()?.classList.contains('filter-menu--up')).toBe(true);
  });

  it('opens downward when there is room below', () => {
    stubLayout(120, 800, 280);
    render();
    press('ArrowDown');
    expect(listbox()).not.toBeNull();
    expect(listbox()?.classList.contains('filter-menu--up')).toBe(false);
  });
});
