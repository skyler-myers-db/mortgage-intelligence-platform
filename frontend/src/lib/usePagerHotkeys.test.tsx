/**
 * @vitest-environment happy-dom
 *
 * usePagerHotkeys on the shared keymap (audit wow-power-4): J / K are
 * `queue-pager` bindings, registered only while their handler exists, live
 * only while focus is inside the page's <main>, off for an auto-repeat, a
 * field, an open overlay and the Console's single-key switch. There is no
 * private window listener left: with the switch off, nothing pages.
 */
import { act, useRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../test/installLocalStorage';
import { mount } from '../test/render';
import { listKeyBindings } from './keymap';
import { clearSingleKeyShortcutsPreference, setSingleKeyShortcutsEnabled } from './keymapPreference';
import { isPagerHotkeyInScope, usePagerHotkeys, type PagerHotkeyHandlers } from './usePagerHotkeys';

function Dossier({ handlers }: { handlers: PagerHotkeyHandlers }) {
  const anchor = useRef<HTMLElement | null>(null);
  usePagerHotkeys(handlers, anchor);
  return (
    <main tabIndex={-1}>
      <h1 tabIndex={-1}>Dossier</h1>
      <input aria-label="note" />
      <nav ref={anchor} aria-label="pager" />
    </main>
  );
}

let next = vi.fn();
let previous = vi.fn();
let outside: HTMLButtonElement;

beforeEach(() => {
  installLocalStorage();
  clearSingleKeyShortcutsPreference();
  next = vi.fn();
  previous = vi.fn();
  outside = document.createElement('button');
  document.body.appendChild(outside);
});

afterEach(() => {
  outside.remove();
});

function key(target: Element, init: KeyboardEventInit): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

function heading(): HTMLElement {
  const h1 = document.querySelector<HTMLElement>('main h1');
  if (!h1) throw new Error('no heading');
  act(() => h1.focus());
  return h1;
}

const pagerBindings = () => listKeyBindings().filter((binding) => binding.scope === 'queue-pager');

describe('usePagerHotkeys', () => {
  it('registers J and K in the queue-pager scope while their handlers exist', async () => {
    const { rerender } = await mount(<Dossier handlers={{ next, previous }} />);
    expect(pagerBindings().map((binding) => [binding.id, binding.keys, binding.description])).toEqual([
      ['queue-pager-next', ['j'], 'Next borrower in the queue'],
      ['queue-pager-previous', ['k'], 'Previous borrower in the queue'],
    ]);
    await rerender(<Dossier handlers={{ next: null, previous }} />);
    expect(pagerBindings().map((binding) => binding.id)).toEqual(['queue-pager-previous']);
  });

  it('J pages next and K previous from inside the dossier, and marks the key handled', async () => {
    await mount(<Dossier handlers={{ next, previous }} />);
    const target = heading();
    expect(key(target, { key: 'j' }).defaultPrevented).toBe(true);
    key(target, { key: 'k' });
    expect(next).toHaveBeenCalledTimes(1);
    expect(previous).toHaveBeenCalledTimes(1);
  });

  it('ignores an auto-repeat, a field, focus outside <main> and an open overlay', async () => {
    await mount(<Dossier handlers={{ next, previous }} />);
    const target = heading();
    key(target, { key: 'j', repeat: true });
    const field = document.querySelector<HTMLInputElement>('main input')!;
    act(() => field.focus());
    key(field, { key: 'j' });
    act(() => outside.focus());
    key(outside, { key: 'j' });
    const menu = document.createElement('div');
    menu.setAttribute('role', 'menu');
    document.body.appendChild(menu);
    key(heading(), { key: 'j' });
    menu.remove();
    expect(next).not.toHaveBeenCalled();
  });

  it('the Console single-key switch turns J and K off (no private listener)', async () => {
    await mount(<Dossier handlers={{ next, previous }} />);
    act(() => setSingleKeyShortcutsEnabled(false));
    const event = key(heading(), { key: 'j' });
    expect(next).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
    act(() => setSingleKeyShortcutsEnabled(true));
    key(heading(), { key: 'j' });
    expect(next).toHaveBeenCalledTimes(1);
  });

  it('calls the freshest handler without re-registering', async () => {
    const { rerender } = await mount(<Dossier handlers={{ next, previous }} />);
    const order = pagerBindings().map((binding) => binding.order);
    const fresher = vi.fn();
    await rerender(<Dossier handlers={{ next: fresher, previous }} />);
    expect(pagerBindings().map((binding) => binding.order)).toEqual(order);
    key(heading(), { key: 'j' });
    expect(fresher).toHaveBeenCalledTimes(1);
    expect(next).not.toHaveBeenCalled();
  });

  it('isPagerHotkeyInScope needs a region and refuses modifier chords', () => {
    const region = document.createElement('main');
    const inside = document.createElement('button');
    region.appendChild(inside);
    document.body.appendChild(region);
    inside.focus();
    const event = (init: KeyboardEventInit) => {
      const e = new KeyboardEvent('keydown', { key: 'j', ...init });
      Object.defineProperty(e, 'target', { value: inside });
      return e;
    };
    expect(isPagerHotkeyInScope(event({}), null)).toBe(false);
    expect(isPagerHotkeyInScope(event({ shiftKey: true }), region)).toBe(false);
    expect(isPagerHotkeyInScope(event({ metaKey: true }), region)).toBe(false);
    expect(isPagerHotkeyInScope(event({}), region)).toBe(true);
    region.remove();
  });
});
