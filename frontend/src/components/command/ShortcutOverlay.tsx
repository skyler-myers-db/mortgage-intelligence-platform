import { Fragment, useId, useRef, useSyncExternalStore } from 'react';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import {
  KEYMAP_SCOPE_LABELS,
  chordKeycaps,
  isModifierChord,
  keyBindingsVersion,
  listKeyBindings,
  parseChord,
  subscribeKeyBindings,
  type KeymapScope,
} from '../../lib/keymap';
import { useSingleKeyShortcuts } from '../../lib/keymapPreference';
import { Icon } from '../Icon';
import './ShortcutOverlay.css';

/**
 * The `?` keyboard-shortcut sheet (audit wow-power-4). Lazy-loaded by
 * ShortcutOverlayHost. It lists exactly what the keymap registry has bound
 * right now, so the Lead Queue's J / K / Enter / X / A / R appear only on a
 * page that shows the ranked-borrower table, and an approver-only key never
 * appears for a session that cannot approve.
 *
 * `.cmdk` BEM (the command palette's modal surface and rows) plus the global
 * `kbd` keycap; the static-row modifier and the keycap cluster live in the
 * colocated ShortcutOverlay.css (shipped with this lazy chunk).
 */
export interface ShortcutOverlayProps {
  onClose: () => void;
}

interface SheetEntry {
  key: string;
  description: string;
  keys: readonly string[];
  singleKey: boolean;
}

/** Page-specific scopes first, then what works everywhere. */
const SCOPE_ORDER: readonly KeymapScope[] = ['lead-queue', 'global'];

function sheetEntries(): Map<KeymapScope, SheetEntry[]> {
  const grouped = new Map<KeymapScope, SheetEntry[]>();
  for (const binding of listKeyBindings()) {
    const entryKey = `${binding.keys.join(' ')}|${binding.description}`;
    const entries = grouped.get(binding.scope) ?? [];
    // Two tables on one page register the same keys: list them once.
    if (entries.some((entry) => entry.key === entryKey)) continue;
    entries.push({
      key: entryKey,
      description: binding.description,
      keys: binding.keys,
      singleKey: binding.keys.every((keys) => !isModifierChord(parseChord(keys))),
    });
    grouped.set(binding.scope, entries);
  }
  return grouped;
}

export function ShortcutOverlay({ onClose }: ShortcutOverlayProps) {
  const titleId = useId();
  const noteId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [singleKeysOn] = useSingleKeyShortcuts();
  // Re-render when a page registers or drops a binding while the sheet is open.
  useSyncExternalStore(subscribeKeyBindings, keyBindingsVersion, keyBindingsVersion);
  useFocusTrap({ open: true, containerRef: panelRef, initialFocusRef: closeRef, onClose });

  const grouped = sheetEntries();
  return (
    <div
      className="cmdk"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="cmdk__panel cmdk__panel--sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={singleKeysOn ? undefined : noteId}
        tabIndex={-1}
        data-testid="shortcut-sheet"
      >
        <div className="cmdk__search">
          <Icon name="tweak" size={14} />
          <h2 id={titleId} className="cmdk__title">Keyboard shortcuts</h2>
          <button
            ref={closeRef}
            type="button"
            className="drawer__close"
            onClick={onClose}
            aria-label="Close keyboard shortcuts"
          >
            <Icon name="close" size={14} />
          </button>
        </div>
        {!singleKeysOn && (
          <p id={noteId} className="cmdk__status" role="note" data-testid="shortcut-sheet-off">
            Single-key shortcuts are off, so only the shortcuts with a modifier key work.
            Turn them back on in the Console.
          </p>
        )}
        <div className="cmdk__list">
          {SCOPE_ORDER.filter((scope) => grouped.has(scope)).map((scope) => {
            const headingId = `${titleId}-${scope}`;
            return (
              <section key={scope} className="cmdk__group" aria-labelledby={headingId}>
                <h3 id={headingId} className="cmdk__group-label">{KEYMAP_SCOPE_LABELS[scope]}</h3>
                <ul className="cmdk__rows">
                  {grouped.get(scope)?.map((entry) => {
                    const off = entry.singleKey && !singleKeysOn;
                    return (
                      <li key={entry.key} className="cmdk__row cmdk__row--static">
                        <span className="cmdk__row-label">{entry.description}</span>
                        <span className="cmdk__keys">
                          {entry.keys.map((chord, index) => (
                            <Fragment key={chord}>
                              {index > 0 && <span className="cmdk__keys-or">or</span>}
                              {chordKeycaps(chord).map((cap) => <kbd key={cap}>{cap}</kbd>)}
                            </Fragment>
                          ))}
                          {off && <span className="cmdk__keys-off">Off</span>}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
          <section className="cmdk__group" aria-label="Panels">
            <ul className="cmdk__rows">
              <li className="cmdk__row cmdk__row--static">
                <span className="cmdk__row-label">Close the topmost panel or menu</span>
                <span className="cmdk__keys"><kbd>Esc</kbd></span>
              </li>
            </ul>
          </section>
        </div>
        <div className="cmdk__footer">
          <span>Shortcuts act only where they are listed; typing in a field never triggers one.</span>
        </div>
      </div>
    </div>
  );
}
