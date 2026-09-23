/**
 * One scoped keyboard-shortcut registry (audit wow-power-4, tables-03).
 *
 * Before this module the shell carried independent `window` keydown
 * listeners (Cmd-K palette, the `/` topbar search, the Lead Queue A / R /
 * Shift+A keys), each with its own editable-target and overlay checks, and
 * nothing could list what was bound. Now:
 *
 *   - `registerKeyBinding(spec)` adds a binding and returns its unregister
 *     (safe as a React effect cleanup; a second call is a no-op).
 *   - ONE bubble-phase `window` keydown listener dispatches. Among the
 *     bindings whose chord matches, the most specific scope wins
 *     (`lead-queue` over `global`), then the latest registration. A binding
 *     whose `when` predicate fails is skipped; a handler that returns
 *     `false` declines and the next candidate runs.
 *   - Editable targets (input, textarea, select, contenteditable) are
 *     ignored unless a binding opts in (`allowInEditable`, for Cmd-K).
 *   - The per-actor "Single-key shortcuts" preference (keymapPreference.ts,
 *     WCAG 2.1.4) switches off every chord without Ctrl / Cmd / Alt; the
 *     modifier chords stay on.
 *   - `listKeyBindings()` / `subscribeKeyBindings()` feed the `?` shortcut
 *     sheet, so it lists exactly what the current page has bound.
 *
 * Escape is NOT bound here: the topmost-layer stack (escapeStack.ts) owns
 * it. `useFocusTrap`, `escapeStack` and the listbox hooks stay
 * component-local by design.
 */
import { singleKeyShortcutsEnabled } from './keymapPreference';

export type KeymapScope = 'global' | 'lead-queue';

/** Higher wins when two active bindings share a chord. */
const SCOPE_SPECIFICITY: Readonly<Record<KeymapScope, number>> = {
  global: 0,
  'lead-queue': 1,
};

export const KEYMAP_SCOPE_LABELS: Readonly<Record<KeymapScope, string>> = {
  global: 'Everywhere',
  'lead-queue': 'Ranked borrowers table',
};

/** Window event the `?` sheet also opens on (the identity menu dispatches it). */
export const OPEN_SHORTCUTS_EVENT = 'mip:open-shortcuts';

export function openShortcutOverlay(): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(OPEN_SHORTCUTS_EVENT));
}

export interface KeyChord {
  /** `event.key` to match; single letters compare case-insensitively. */
  key: string;
  /** Cmd on macOS, Ctrl elsewhere (either is accepted). */
  mod: boolean;
  ctrl: boolean;
  meta: boolean;
  alt: boolean;
  /** true = Shift required; false = Shift must be up; null = either. */
  shift: boolean | null;
}

export interface KeyBindingSpec {
  /** Stable id (overlay de-duplication, diagnostics). */
  id: string;
  scope: KeymapScope;
  /** Chords, e.g. `['j', 'ArrowDown']`, `['Shift+A']`, `['Mod+K']`, `['?']`. */
  keys: readonly string[];
  /** What the shortcut does, as the `?` sheet lists it. */
  description: string;
  /** The binding applies only while this returns true (scope activation). */
  when?: (event: KeyboardEvent) => boolean;
  /** Return `false` to decline, so the next candidate runs. */
  run: (event: KeyboardEvent) => boolean | void;
  /** Fire even while typing in an editable field (modifier chords only). */
  allowInEditable?: boolean;
}

export interface RegisteredKeyBinding extends KeyBindingSpec {
  readonly order: number;
  readonly chords: readonly KeyChord[];
}

const NAMED_KEYS: Readonly<Record<string, string>> = {
  arrowdown: 'ArrowDown',
  arrowup: 'ArrowUp',
  arrowleft: 'ArrowLeft',
  arrowright: 'ArrowRight',
  enter: 'Enter',
  space: ' ',
  tab: 'Tab',
  home: 'Home',
  end: 'End',
};

function isLetter(key: string): boolean {
  return /^[a-z]$/i.test(key);
}

/** Parse `Mod+K`, `Shift+A`, `j`, `?`, `ArrowDown`. Throws on an empty chord. */
export function parseChord(text: string): KeyChord {
  const parts = text.split('+');
  const rawKey = parts.pop() ?? '';
  const modifiers = parts.map((part) => part.toLowerCase());
  if (!rawKey) throw new Error(`Empty key chord "${text}"`);
  const key = NAMED_KEYS[rawKey.toLowerCase()] ?? rawKey;
  const chord: KeyChord = {
    key,
    mod: modifiers.includes('mod'),
    ctrl: modifiers.includes('ctrl'),
    meta: modifiers.includes('meta') || modifiers.includes('cmd'),
    alt: modifiers.includes('alt'),
    shift: modifiers.includes('shift') ? true : null,
  };
  if (chord.shift === null) {
    const printable = key.length === 1 && !isLetter(key);
    // A symbol such as `?` or `/` is typed WITH Shift on many layouts, and a
    // modifier chord such as Cmd-K tolerates Shift; a bare letter or a named
    // key must not also fire for its Shift twin (`a` is not `Shift+A`).
    chord.shift = printable || isModifierChord(chord) ? null : false;
  }
  return chord;
}

/** A chord with Ctrl / Cmd / Alt: never a WCAG 2.1.4 single-character shortcut. */
export function isModifierChord(chord: KeyChord): boolean {
  return chord.mod || chord.ctrl || chord.meta || chord.alt;
}

export function matchesChord(chord: KeyChord, event: KeyboardEvent): boolean {
  const key = event.key ?? '';
  const keyMatches = isLetter(chord.key)
    ? key.toLowerCase() === chord.key.toLowerCase()
    : key === chord.key;
  if (!keyMatches) return false;
  if (chord.mod) {
    if (!event.metaKey && !event.ctrlKey) return false;
  } else if (event.ctrlKey !== chord.ctrl || event.metaKey !== chord.meta) {
    return false;
  }
  if (event.altKey !== chord.alt) return false;
  if (chord.shift !== null && event.shiftKey !== chord.shift) return false;
  return true;
}

const IS_MAC = typeof navigator !== 'undefined'
  && /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || '');

const KEY_LABELS: Readonly<Record<string, string>> = {
  ArrowDown: '↓',
  ArrowUp: '↑',
  ArrowLeft: '←',
  ArrowRight: '→',
  Enter: 'Enter',
  ' ': 'Space',
};

/** The keycaps one chord renders as in the sheet: `Mod+K` → ['⌘', 'K']. */
export function chordKeycaps(text: string): string[] {
  const chord = parseChord(text);
  const caps: string[] = [];
  if (chord.mod) caps.push(IS_MAC ? '⌘' : 'Ctrl');
  if (chord.ctrl) caps.push('Ctrl');
  if (chord.meta) caps.push(IS_MAC ? '⌘' : 'Meta');
  if (chord.alt) caps.push(IS_MAC ? '⌥' : 'Alt');
  if (chord.shift === true) caps.push('Shift');
  caps.push(KEY_LABELS[chord.key] ?? (chord.key.length === 1 ? chord.key.toUpperCase() : chord.key));
  return caps;
}

/** `aria-keyshortcuts` value for a set of chords (`Mod` becomes Meta / Control). */
export function ariaKeyShortcuts(keys: readonly string[]): string {
  return keys
    .map((text) => {
      const chord = parseChord(text);
      const parts: string[] = [];
      if (chord.mod) parts.push(IS_MAC ? 'Meta' : 'Control');
      if (chord.ctrl) parts.push('Control');
      if (chord.meta) parts.push('Meta');
      if (chord.alt) parts.push('Alt');
      if (chord.shift === true) parts.push('Shift');
      parts.push(chord.key.length === 1 ? chord.key.toUpperCase() : chord.key);
      return parts.join('+');
    })
    .join(' ');
}

/** Input, textarea, select or contenteditable: where typing must win. */
export function isEditableElement(el: Element | null | undefined): boolean {
  if (!el) return false;
  const tag = (el as HTMLElement).tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return (el as HTMLElement).isContentEditable === true;
}

/**
 * Anything that, while open, owns the keyboard: modal and non-modal dialogs
 * (evidence drawer, borrower proof drawer, command palette, the floating
 * Genie panel, the shortcut sheet), listboxes (filter menus, topbar search
 * results) and menus. The always-mounted drawers and the Genie panel toggle
 * `aria-hidden`, so a closed one never matches.
 */
const OPEN_OVERLAY_SELECTOR = [
  '[role="dialog"]',
  '[role="alertdialog"]',
  '[role="listbox"]',
  '[role="menu"]',
  'dialog[open]',
].join(',');

export function hasOpenOverlay(doc: Document = document): boolean {
  for (const el of doc.querySelectorAll(OPEN_OVERLAY_SELECTOR)) {
    if (el.closest('[aria-hidden="true"]') === null) return true;
  }
  return false;
}

/** A modal layer is open (drawer, palette, shortcut sheet, a native modal dialog). */
export function hasOpenModal(doc: Document = document): boolean {
  for (const el of doc.querySelectorAll('[aria-modal="true"], dialog[open]')) {
    if (el.closest('[aria-hidden="true"]') === null) return true;
  }
  return false;
}

const bindings: RegisteredKeyBinding[] = [];
const subscribers = new Set<() => void>();
let nextOrder = 1;
let version = 0;
let listening = false;

function candidatesFor(event: KeyboardEvent): RegisteredKeyBinding[] {
  const target = event.target instanceof Element ? event.target : null;
  const active = typeof document === 'undefined' ? null : document.activeElement;
  const editable = isEditableElement(target) || isEditableElement(active);
  const singleKeys = singleKeyShortcutsEnabled();
  return bindings
    .filter((binding) => binding.chords.some((chord) => (
      matchesChord(chord, event) && (singleKeys || isModifierChord(chord))
    )))
    .filter((binding) => !editable || binding.allowInEditable === true)
    .sort((a, b) => (
      SCOPE_SPECIFICITY[b.scope] - SCOPE_SPECIFICITY[a.scope] || b.order - a.order
    ));
}

function onWindowKeyDown(event: KeyboardEvent): void {
  if (event.defaultPrevented || event.isComposing) return;
  for (const binding of candidatesFor(event)) {
    if (binding.when && !binding.when(event)) continue;
    if (binding.run(event) === false) continue;
    event.preventDefault();
    return;
  }
}

function syncListener(): void {
  if (typeof window === 'undefined') return;
  if (bindings.length > 0 && !listening) {
    window.addEventListener('keydown', onWindowKeyDown);
    listening = true;
  } else if (bindings.length === 0 && listening) {
    window.removeEventListener('keydown', onWindowKeyDown);
    listening = false;
  }
}

function changed(): void {
  version += 1;
  syncListener();
  subscribers.forEach((callback) => callback());
}

export function registerKeyBinding(spec: KeyBindingSpec): () => void {
  const registered: RegisteredKeyBinding = {
    ...spec,
    order: nextOrder,
    chords: spec.keys.map(parseChord),
  };
  nextOrder += 1;
  bindings.push(registered);
  changed();
  return () => {
    const index = bindings.indexOf(registered);
    if (index === -1) return;
    bindings.splice(index, 1);
    changed();
  };
}

/** The bindings registered right now (the current page), in registration order. */
export function listKeyBindings(): readonly RegisteredKeyBinding[] {
  return [...bindings];
}

export function subscribeKeyBindings(callback: () => void): () => void {
  subscribers.add(callback);
  return () => {
    subscribers.delete(callback);
  };
}

/** Changes whenever a binding is added or removed (useSyncExternalStore snapshot). */
export function keyBindingsVersion(): number {
  return version;
}
