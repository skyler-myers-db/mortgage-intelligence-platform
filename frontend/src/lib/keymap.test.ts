/**
 * @vitest-environment happy-dom
 *
 * The scoped keymap registry (audit wow-power-4): chord parsing, the
 * most-specific-scope-wins dispatch, editable targets, the per-actor
 * single-key switch (WCAG 2.1.4) and the binding list the `?` sheet reads.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../test/installLocalStorage';
import { clearActorScopedBrowserState } from './actorScopedBrowserState';
import {
  ariaKeyShortcuts,
  chordKeycaps,
  hasOpenModal,
  hasOpenOverlay,
  isEditableElement,
  isModifierChord,
  keyBindingsVersion,
  listKeyBindings,
  matchesChord,
  parseChord,
  registerKeyBinding,
  subscribeKeyBindings,
  type KeyBindingSpec,
} from './keymap';
import {
  SINGLE_KEY_SHORTCUTS_STORAGE_KEY,
  clearSingleKeyShortcutsPreference,
  setSingleKeyShortcutsEnabled,
  singleKeyShortcutsEnabled,
  subscribeSingleKeyShortcuts,
} from './keymapPreference';

function keydown(init: KeyboardEventInit): KeyboardEvent {
  return new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
}

function press(target: EventTarget, init: KeyboardEventInit): KeyboardEvent {
  const event = keydown(init);
  target.dispatchEvent(event);
  return event;
}

describe('chords', () => {
  it('a bare letter never fires for its Shift twin, and Shift+A needs Shift', () => {
    const a = parseChord('a');
    const shiftA = parseChord('Shift+A');
    expect(matchesChord(a, keydown({ key: 'a' }))).toBe(true);
    expect(matchesChord(a, keydown({ key: 'A', shiftKey: true }))).toBe(false);
    expect(matchesChord(shiftA, keydown({ key: 'A', shiftKey: true }))).toBe(true);
    expect(matchesChord(shiftA, keydown({ key: 'a' }))).toBe(false);
    // A bare letter never fires under Ctrl / Cmd / Alt (browser and SR keys).
    expect(matchesChord(a, keydown({ key: 'a', ctrlKey: true }))).toBe(false);
    expect(matchesChord(a, keydown({ key: 'a', metaKey: true }))).toBe(false);
    expect(matchesChord(a, keydown({ key: 'a', altKey: true }))).toBe(false);
  });

  it('a symbol typed with Shift still matches, and Mod accepts Cmd or Ctrl', () => {
    expect(matchesChord(parseChord('?'), keydown({ key: '?', shiftKey: true }))).toBe(true);
    const modK = parseChord('Mod+K');
    expect(matchesChord(modK, keydown({ key: 'k', metaKey: true }))).toBe(true);
    expect(matchesChord(modK, keydown({ key: 'K', ctrlKey: true }))).toBe(true);
    expect(matchesChord(modK, keydown({ key: 'k' }))).toBe(false);
  });

  it('named keys parse case-insensitively and refuse modifiers they do not name', () => {
    const down = parseChord('arrowdown');
    expect(down.key).toBe('ArrowDown');
    expect(matchesChord(down, keydown({ key: 'ArrowDown' }))).toBe(true);
    expect(matchesChord(down, keydown({ key: 'ArrowDown', shiftKey: true }))).toBe(false);
  });

  it('classifies modifier chords (WCAG 2.1.4: only these survive the off switch)', () => {
    expect(isModifierChord(parseChord('Mod+K'))).toBe(true);
    expect(isModifierChord(parseChord('Alt+J'))).toBe(true);
    expect(isModifierChord(parseChord('Shift+A'))).toBe(false);
    expect(isModifierChord(parseChord('ArrowDown'))).toBe(false);
  });

  it('renders keycaps and aria-keyshortcuts values', () => {
    expect(chordKeycaps('Shift+A')).toEqual(['Shift', 'A']);
    expect(chordKeycaps('ArrowDown')).toEqual(['↓']);
    expect(chordKeycaps('j')).toEqual(['J']);
    expect(ariaKeyShortcuts(['j', 'ArrowDown', 'Shift+A'])).toBe('J ArrowDown Shift+A');
  });
});

describe('registry dispatch', () => {
  const cleanups: Array<() => void> = [];
  const register = (spec: KeyBindingSpec) => {
    const off = registerKeyBinding(spec);
    cleanups.push(off);
    return off;
  };

  beforeEach(() => {
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
  });

  afterEach(() => {
    while (cleanups.length > 0) cleanups.pop()?.();
    document.body.innerHTML = '';
    expect(listKeyBindings()).toHaveLength(0);
  });

  it('the most specific active scope wins, and preventDefault marks it handled', () => {
    const global = vi.fn();
    const queue = vi.fn();
    register({ id: 'g', scope: 'global', keys: ['j'], description: 'global j', run: global });
    register({ id: 'q', scope: 'lead-queue', keys: ['j'], description: 'queue j', run: queue });

    const event = press(document.body, { key: 'j' });

    expect(queue).toHaveBeenCalledOnce();
    expect(global).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(true);
  });

  it('an inactive scope (when=false) or a declining handler falls through', () => {
    const global = vi.fn();
    register({ id: 'g', scope: 'global', keys: ['j'], description: 'global j', run: global });
    register({ id: 'q', scope: 'lead-queue', keys: ['j'], description: 'queue j', when: () => false, run: vi.fn() });
    press(document.body, { key: 'j' });
    expect(global).toHaveBeenCalledTimes(1);

    register({ id: 'q2', scope: 'lead-queue', keys: ['j'], description: 'declines', run: () => false });
    const event = press(document.body, { key: 'j' });
    expect(global).toHaveBeenCalledTimes(2);
    expect(event.defaultPrevented).toBe(true);
  });

  it('ignores editable targets unless the binding opts in', () => {
    const letter = vi.fn();
    const palette = vi.fn();
    register({ id: 'l', scope: 'global', keys: ['/'], description: 'slash', run: letter });
    register({ id: 'p', scope: 'global', keys: ['Mod+K'], description: 'palette', allowInEditable: true, run: palette });
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.focus();

    press(input, { key: '/' });
    press(input, { key: 'k', metaKey: true });

    expect(letter).not.toHaveBeenCalled();
    expect(palette).toHaveBeenCalledOnce();
  });

  // Wave-3 review #9: a focused row checkbox (or radio) takes no typing, so
  // it must not switch the table's J / K / X / Enter off. Text fields,
  // select, textarea, contenteditable and range inputs still do.
  it('a focused checkbox or radio is not an editable target; typing fields and range still are', () => {
    const next = vi.fn();
    register({ id: 'j', scope: 'lead-queue', keys: ['j'], description: 'next', run: next });
    for (const type of ['checkbox', 'radio']) {
      const box = document.createElement('input');
      box.type = type;
      document.body.appendChild(box);
      box.focus();
      press(box, { key: 'j' });
    }
    expect(next).toHaveBeenCalledTimes(2);
    expect(isEditableElement(Object.assign(document.createElement('input'), { type: 'checkbox' }))).toBe(false);

    for (const make of [
      () => document.createElement('input'),
      () => Object.assign(document.createElement('input'), { type: 'range' }),
      () => Object.assign(document.createElement('input'), { type: 'search' }),
      () => document.createElement('textarea'),
      () => document.createElement('select'),
    ]) {
      const field = make();
      document.body.appendChild(field);
      field.focus();
      press(field, { key: 'j' });
      expect(isEditableElement(field), field.outerHTML).toBe(true);
    }
    expect(next).toHaveBeenCalledTimes(2);
  });

  it('skips an event another handler already consumed', () => {
    const run = vi.fn();
    register({ id: 'd', scope: 'global', keys: ['ArrowDown'], description: 'down', run });
    const host = document.createElement('div');
    document.body.appendChild(host);
    host.addEventListener('keydown', (event) => event.preventDefault());

    press(host, { key: 'ArrowDown' });

    expect(run).not.toHaveBeenCalled();
  });

  it('the single-key switch turns every non-modifier binding off and leaves Cmd-K on', () => {
    const single = vi.fn();
    const shifted = vi.fn();
    const modifier = vi.fn();
    register({ id: 's', scope: 'lead-queue', keys: ['a', 'ArrowDown'], description: 'a', run: single });
    register({ id: 'sa', scope: 'lead-queue', keys: ['Shift+A'], description: 'shift a', run: shifted });
    register({ id: 'm', scope: 'global', keys: ['Mod+K'], description: 'palette', run: modifier });

    setSingleKeyShortcutsEnabled(false);
    press(document.body, { key: 'a' });
    press(document.body, { key: 'ArrowDown' });
    press(document.body, { key: 'A', shiftKey: true });
    press(document.body, { key: 'k', ctrlKey: true });
    expect(single).not.toHaveBeenCalled();
    expect(shifted).not.toHaveBeenCalled();
    expect(modifier).toHaveBeenCalledOnce();
    expect(window.localStorage.getItem(SINGLE_KEY_SHORTCUTS_STORAGE_KEY)).toBe('off');

    setSingleKeyShortcutsEnabled(true);
    press(document.body, { key: 'a' });
    expect(single).toHaveBeenCalledOnce();
  });

  it('lists what is bound now, notifies on change, and unregister is idempotent', () => {
    const seen = vi.fn();
    const unsubscribe = subscribeKeyBindings(seen);
    const before = keyBindingsVersion();
    const off = register({ id: 'x', scope: 'lead-queue', keys: ['x'], description: 'Select the row', run: vi.fn() });

    expect(listKeyBindings().map((binding) => binding.description)).toEqual(['Select the row']);
    expect(keyBindingsVersion()).toBeGreaterThan(before);
    off();
    off();
    expect(listKeyBindings()).toHaveLength(0);
    expect(seen).toHaveBeenCalledTimes(2);
    unsubscribe();
  });

  it('removes its window listener when the last binding goes away', () => {
    const add = vi.spyOn(window, 'addEventListener');
    const remove = vi.spyOn(window, 'removeEventListener');
    const off = registerKeyBinding({ id: 'z', scope: 'global', keys: ['z'], description: 'z', run: vi.fn() });
    expect(add).toHaveBeenCalledWith('keydown', expect.any(Function));
    off();
    expect(remove).toHaveBeenCalledWith('keydown', expect.any(Function));
    add.mockRestore();
    remove.mockRestore();
  });
});

describe('overlay state', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('counts an open dialog, listbox or menu, never an aria-hidden one', () => {
    document.body.innerHTML = '<aside role="dialog" aria-modal="true" aria-hidden="true"></aside>';
    expect(hasOpenOverlay(document)).toBe(false);
    expect(hasOpenModal(document)).toBe(false);
    document.body.innerHTML += '<ul role="listbox"></ul>';
    expect(hasOpenOverlay(document)).toBe(true);
    expect(hasOpenModal(document)).toBe(false);
    document.body.innerHTML += '<div role="dialog" aria-modal="true"></div>';
    expect(hasOpenModal(document)).toBe(true);
  });

  /**
   * Every modal surface is now a native <dialog> with no role attribute
   * (audit stack-05): the drawers, the palette, the shortcut sheet, the offer
   * mock. An open one is `dialog[open]`; a closed or closing one (kept
   * mounted for its exit) has no `open` and is aria-hidden. So Cmd-K over an
   * open drawer, proof drawer, offer mock or review still opens nothing,
   * and a closed dialog switches no key off.
   */
  it('counts an open <dialog> as a modal and an overlay; a closed or closing one as neither', () => {
    document.body.innerHTML = '<dialog class="drawer is-open" open aria-label="Data source and lineage"></dialog>';
    expect(hasOpenModal(document)).toBe(true);
    expect(hasOpenOverlay(document)).toBe(true);

    // Closing: close() ran at exit start, the retained copy is aria-hidden.
    document.body.innerHTML = '<dialog class="drawer" aria-hidden="true" inert aria-label="Data source and lineage"></dialog>';
    expect(hasOpenModal(document)).toBe(false);
    expect(hasOpenOverlay(document)).toBe(false);

    // An open dialog inside an aria-hidden subtree is not the user's layer.
    document.body.innerHTML = '<div aria-hidden="true"><dialog open></dialog></div>';
    expect(hasOpenModal(document)).toBe(false);
  });

  it('never counts a closed <dialog> without a role attribute, even one holding a listbox, as an overlay', () => {
    // The always-mounted, closed palette: its listbox lives in the dialog.
    document.body.innerHTML = [
      '<dialog class="cmdk" aria-label="Command palette" aria-hidden="true" inert>',
      '<input role="combobox" aria-expanded="true"><div role="listbox"></div>',
      '</dialog>',
    ].join('');
    expect(hasOpenOverlay(document)).toBe(false);
    expect(hasOpenModal(document)).toBe(false);
    // The same palette open: a modal layer, and Cmd-K's own toggle still
    // closes it (CommandPalette reads its open state before hasOpenModal).
    const palette = document.querySelector('dialog') as HTMLDialogElement;
    palette.removeAttribute('aria-hidden');
    palette.removeAttribute('inert');
    palette.setAttribute('open', '');
    expect(hasOpenModal(document)).toBe(true);
    expect(hasOpenOverlay(document)).toBe(true);
  });

  it('never counts an overlay that is not rendered, such as an always-mounted hidden listbox', () => {
    document.body.innerHTML = '<ul role="listbox" hidden></ul>';
    const listbox = document.querySelector('ul') as HTMLElement;
    // What a browser answers for `hidden` / `display: none`.
    listbox.checkVisibility = () => false;
    expect(hasOpenOverlay(document)).toBe(false);
    listbox.checkVisibility = () => true;
    expect(hasOpenOverlay(document)).toBe(true);
  });
});

describe('single-key preference', () => {
  beforeEach(() => {
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
  });

  it('defaults on, persists off, and an actor change resets it and notifies', () => {
    expect(singleKeyShortcutsEnabled()).toBe(true);
    const seen = vi.fn();
    const unsubscribe = subscribeSingleKeyShortcuts(seen);
    setSingleKeyShortcutsEnabled(false);
    expect(singleKeyShortcutsEnabled()).toBe(false);

    clearActorScopedBrowserState();

    expect(window.localStorage.getItem(SINGLE_KEY_SHORTCUTS_STORAGE_KEY)).toBeNull();
    expect(singleKeyShortcutsEnabled()).toBe(true);
    expect(seen).toHaveBeenCalledTimes(2);
    unsubscribe();
  });
});
