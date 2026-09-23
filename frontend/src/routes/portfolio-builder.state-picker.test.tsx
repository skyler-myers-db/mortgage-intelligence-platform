/**
 * @vitest-environment happy-dom
 *
 * 2026-09-21 audit a11y-v1 (WCAG 2.1.1 Level A). The Portfolio Builder GEO
 * picker was a private listbox whose options were `li role=option` with
 * onClick and nothing else: no tabIndex, no key handler, no Escape. A keyboard
 * user could open it and then do nothing, so step one of the product flow
 * (choose the states) was mouse-only.
 *
 * These tests drive the component the route renders (`StateMultiSelect`) with
 * the keyboard alone. No test here clicks.
 */

import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StateMultiSelect } from './portfolio-builder.components';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const STATES = [
  { state_code: 'AZ', state_name: 'Arizona' },
  { state_code: 'IL', state_name: 'Illinois' },
  { state_code: 'TX', state_name: 'Texas' },
];

const changes = vi.fn<(next: string[]) => void>();

function Harness({ initial = [] as string[] }) {
  const [value, setValue] = useState<string[]>(initial);
  return (
    <>
      <StateMultiSelect
        label="GEO"
        allLabel="All 3 states"
        states={STATES}
        value={value}
        onChange={(next) => {
          changes(next);
          setValue(next);
        }}
      />
      <button type="button" id="after">Next control</button>
    </>
  );
}

function press(key: string, init: KeyboardEventInit = {}): void {
  const target = document.activeElement ?? document.body;
  act(() => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }));
  });
}

const trigger = (): HTMLButtonElement =>
  document.querySelector<HTMLButtonElement>('button.filter') as HTMLButtonElement;
const listbox = (): HTMLElement | null => document.querySelector<HTMLElement>('[role="listbox"]');
const options = (): HTMLElement[] =>
  [...document.querySelectorAll<HTMLElement>('[role="option"]')];
// a11y-02: the listbox holds DOM focus while open, so IT carries
// aria-activedescendant; the trigger button never does.
const activeOption = (): HTMLElement | null => {
  const id = listbox()?.getAttribute('aria-activedescendant');
  return id ? document.getElementById(id) : null;
};

describe('Portfolio Builder state picker — keyboard operation', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    changes.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function render(initial: string[] = []): void {
    act(() => root.render(<Harness initial={initial} />));
    trigger().focus();
  }

  it('ArrowDown opens the listbox and lands the active option on a real, focused option', () => {
    render();
    expect(listbox()).toBeNull();
    expect(trigger().getAttribute('aria-expanded')).toBe('false');

    press('ArrowDown');

    expect(trigger().getAttribute('aria-expanded')).toBe('true');
    expect(listbox()?.getAttribute('aria-multiselectable')).toBe('true');
    expect(trigger().getAttribute('aria-controls')).toBe(listbox()?.id);
    expect(options().map((option) => option.textContent)).toEqual([
      'All 3 states',
      'Arizona',
      'Illinois',
      'Texas',
    ]);
    // aria-activedescendant must resolve to an element on the focused listbox.
    expect(activeOption()).toBe(options()[0]);
    expect(document.activeElement).toBe(listbox());
    expect(trigger().hasAttribute('aria-activedescendant')).toBe(false);
  });

  it('arrow keys, Home and End move the active option (aria-activedescendant changes)', () => {
    render();
    press('ArrowDown');
    const first = listbox()?.getAttribute('aria-activedescendant');

    press('ArrowDown');
    expect(listbox()?.getAttribute('aria-activedescendant')).not.toBe(first);
    expect(activeOption()?.textContent).toBe('Arizona');
    expect(document.activeElement).toBe(listbox());

    press('ArrowDown');
    expect(activeOption()?.textContent).toBe('Illinois');
    press('ArrowUp');
    expect(activeOption()?.textContent).toBe('Arizona');
    press('End');
    expect(activeOption()?.textContent).toBe('Texas');
    press('ArrowDown');
    expect(activeOption()?.textContent).toBe('All 3 states');
    press('Home');
    expect(activeOption()?.textContent).toBe('All 3 states');

    // The focused listbox is the one tab stop; options are not tabbable.
    expect(listbox()?.tabIndex).toBe(0);
    expect(options().filter((option) => option.tabIndex >= 0)).toHaveLength(0);
  });

  it('Space toggles a state, keeps the menu open, and leaves the active option where the user is', () => {
    render();
    press('ArrowDown');
    press('ArrowDown'); // Arizona
    press(' ');

    expect(changes).toHaveBeenLastCalledWith(['AZ']);
    expect(options()[1].getAttribute('aria-selected')).toBe('true');
    expect(trigger().getAttribute('aria-label')).toBe('GEO: Arizona');
    expect(listbox()).not.toBeNull();

    press('End'); // Texas
    press(' ');
    expect(changes).toHaveBeenLastCalledWith(['AZ', 'TX']);
    expect(trigger().getAttribute('aria-label')).toBe('GEO: 2 states');
    // The active option stays on Texas; it must not jump back up to Arizona.
    expect(activeOption()?.textContent).toBe('Texas');
    expect(activeOption()).toBe(options()[3]);

    press(' ');
    expect(changes).toHaveBeenLastCalledWith(['AZ']);
    expect(options()[3].getAttribute('aria-selected')).toBe('false');

    press('Home');
    press('Enter');
    expect(changes).toHaveBeenLastCalledWith([]);
    expect(trigger().getAttribute('aria-label')).toBe('GEO: All 3 states');
  });

  it('collapses "every state selected" to the footprint default', () => {
    render(['AZ', 'IL']);
    press('ArrowDown');
    expect(activeOption()?.textContent).toBe('Arizona'); // opens on the first selected state
    press('End');
    press(' ');

    expect(changes).toHaveBeenLastCalledWith([]);
    expect(trigger().getAttribute('aria-label')).toBe('GEO: All 3 states');
  });

  it('Escape closes the listbox and returns focus to the trigger', () => {
    render();
    press('ArrowDown');
    press('ArrowDown');
    expect(document.activeElement).not.toBe(trigger());

    press('Escape');

    expect(listbox()).toBeNull();
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    expect(trigger().hasAttribute('aria-activedescendant')).toBe(false);
    expect(document.activeElement).toBe(trigger());
  });

  it('Tab closes the listbox without swallowing the key, so focus moves on from the trigger', () => {
    render();
    press('ArrowDown');
    press('ArrowDown');
    // Non-vacuity: the menu really is open with focus inside it. Against the
    // old mouse-only picker nothing opened, so "closed after Tab" proved nothing.
    expect(listbox()).not.toBeNull();
    expect(document.activeElement).toBe(listbox());
    expect(activeOption()).toBe(options()[1]);

    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    act(() => {
      document.activeElement?.dispatchEvent(tab);
    });

    expect(listbox()).toBeNull();
    // Not prevented: the browser's own Tab carries on. Focus is parked on the
    // trigger first so the next stop is the control after the picker, not
    // wherever the browser lands after the focused option unmounts.
    expect(tab.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(trigger());
  });
});
