import {
  useEffect,
  useEffectEvent,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from 'react';
import { pushEscapeLayer } from '../../lib/escapeStack';

/**
 * One focus-managed listbox for every hand-rolled popup list in the app
 * (2026-09-21 audit a11y-02 / stack-05 / tables-06 / shell-07). Before it,
 * five listboxes each carried their own keyboard code and three gave screen
 * readers no active-option cue. The behaviour extracted here is the
 * MultiFilterSelect one, corrected: `aria-activedescendant` belongs on the
 * element that really holds DOM focus, never on a plain trigger button.
 *
 * The hook is agnostic about WHICH element owns focus; the caller spreads
 * `onKeyDown` and `activeDescendant` onto it:
 *
 *   - select-only combobox (FilterSelect): the `role="combobox"` trigger,
 *   - multi-select listbox (MultiFilterSelect): the `role="listbox"` itself,
 *   - editable combobox (Topbar search, `editable: true`): the text input.
 *
 * Keys: ArrowDown / ArrowUp move (wrapping), Home / End jump (not in an
 * editable combobox, where they move the caret), printable characters run a
 * typeahead over `labels` with a {@link TYPEAHEAD_RESET_MS} buffer (Space
 * joins a running search), Enter / Space commit, Tab and focus leaving
 * `rootRef` close, an outside mousedown closes, and Escape closes through the
 * shared topmost-layer stack (lib/escapeStack.ts) and returns focus to
 * `returnFocusRef`, so one Escape never also closes the Genie panel beneath.
 */

/** Characters typed within this window extend one typeahead search (APG listbox). */
export const TYPEAHEAD_RESET_MS = 500;

export interface ListboxNavigationOptions {
  /** Number of options currently rendered. */
  count: number;
  /** Whether the popup is displayed. Controlled by the caller. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Commit the option at `index`: select it, toggle it, or open it. */
  onCommit: (index: number) => void;
  /** The widget's outer element; focus leaving it (or a mousedown outside it) closes the popup. */
  rootRef: RefObject<HTMLElement | null>;
  /** Where focus goes when Escape closes the popup (the trigger or the input). */
  returnFocusRef: RefObject<HTMLElement | null>;
  /** Visible option labels, in order. Enables typeahead; omit it where typing edits text. */
  labels?: readonly string[];
  /** Multi-select: a commit keeps the popup open. */
  multiple?: boolean;
  /**
   * Editable combobox: Space types, Home / End move the caret, typeahead is
   * off, and Enter with no active option is left to the form.
   */
  editable?: boolean;
  /** Option to activate when a key opens the popup (the current selection). Default 0. */
  initialIndex?: number;
}

export interface ListboxNavigation {
  listboxId: string;
  optionId: (index: number) => string;
  /** Active (visually focused) option, clamped to `count`; -1 when none. */
  activeIndex: number;
  /** `aria-activedescendant` for the focus owner while an option is active. */
  activeDescendant: string | undefined;
  setActiveIndex: (index: number) => void;
  /** Open the popup with `index` active. */
  openAt: (index: number) => void;
  /** Close the popup; `restoreFocus` moves focus back to `returnFocusRef`. */
  close: (restoreFocus?: boolean) => void;
  /** Keydown handler for the element that owns DOM focus. */
  onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => void;
}

function isTypeaheadKey(event: ReactKeyboardEvent<HTMLElement>): boolean {
  return event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
}

/**
 * The next option whose label starts with `buffer`. A buffer of one repeated
 * character cycles through the options starting with it; a longer buffer
 * refines the match from the active option onwards.
 */
export function typeaheadMatch(labels: readonly string[], buffer: string, activeIndex: number): number {
  const count = labels.length;
  if (count === 0 || buffer.length === 0) return -1;
  const lower = buffer.toLowerCase();
  const repeated = [...lower].every((char) => char === lower[0]);
  const needle = repeated ? lower[0] : lower;
  const start = repeated ? activeIndex + 1 : Math.max(activeIndex, 0);
  for (let step = 0; step < count; step += 1) {
    const index = (((start + step) % count) + count) % count;
    if (labels[index].toLowerCase().startsWith(needle)) return index;
  }
  return -1;
}

export function useListboxNavigation({
  count,
  open,
  onOpenChange,
  onCommit,
  rootRef,
  returnFocusRef,
  labels,
  multiple = false,
  editable = false,
  initialIndex = 0,
}: ListboxNavigationOptions): ListboxNavigation {
  const listboxId = useId();
  const [rawActiveIndex, setActiveIndex] = useState(-1);
  const bufferRef = useRef('');
  const bufferTimerRef = useRef<number | undefined>(undefined);
  // Options can shrink under an open popup (a footprint resolving, a new
  // search result set); keep the active index on an option that exists.
  const activeIndex = count === 0 ? -1 : Math.min(rawActiveIndex, count - 1);
  const optionId = (index: number) => `${listboxId}-option-${index}`;
  const activeDescendant = open && activeIndex >= 0 ? optionId(activeIndex) : undefined;

  const resetTypeahead = () => {
    window.clearTimeout(bufferTimerRef.current);
    bufferRef.current = '';
  };

  const close = (restoreFocus = false) => {
    resetTypeahead();
    onOpenChange(false);
    if (restoreFocus) returnFocusRef.current?.focus();
  };

  const openAt = (index: number) => {
    setActiveIndex(index);
    onOpenChange(true);
  };

  const commit = (index: number) => {
    onCommit(index);
    if (!multiple) close(false);
  };

  const typeahead = (char: string): number => {
    window.clearTimeout(bufferTimerRef.current);
    bufferRef.current += char;
    bufferTimerRef.current = window.setTimeout(() => {
      bufferRef.current = '';
    }, TYPEAHEAD_RESET_MS);
    return typeaheadMatch(labels ?? [], bufferRef.current, open ? activeIndex : -1);
  };

  const step = (delta: 1 | -1) => {
    if (activeIndex < 0) return delta === 1 ? 0 : count - 1;
    return (activeIndex + delta + count) % count;
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    const { key } = event;
    const typing = labels !== undefined && !editable && isTypeaheadKey(event);
    // Space extends a running typeahead ("In the Money"); otherwise it commits.
    if (typing && (key !== ' ' || bufferRef.current.length > 0)) {
      event.preventDefault();
      const match = typeahead(key);
      if (!open) openAt(match >= 0 ? match : initialIndex);
      else if (match >= 0) setActiveIndex(match);
      return;
    }

    if (!open) {
      if (count === 0) return;
      if (key === 'ArrowDown' || key === 'ArrowUp') {
        event.preventDefault();
        if (editable) openAt(key === 'ArrowDown' ? 0 : count - 1);
        else openAt(initialIndex);
      } else if (!editable && (key === 'Enter' || key === ' ')) {
        event.preventDefault();
        openAt(initialIndex);
      } else if (!editable && (key === 'Home' || key === 'End')) {
        event.preventDefault();
        openAt(key === 'Home' ? 0 : count - 1);
      }
      return;
    }

    if (key === 'ArrowDown' || key === 'ArrowUp') {
      event.preventDefault();
      if (count > 0) setActiveIndex(step(key === 'ArrowDown' ? 1 : -1));
    } else if (!editable && (key === 'Home' || key === 'End')) {
      event.preventDefault();
      if (count > 0) setActiveIndex(key === 'Home' ? 0 : count - 1);
    } else if (key === 'Enter' || (!editable && key === ' ')) {
      // An editable combobox with nothing highlighted leaves Enter to its form.
      if (activeIndex < 0) return;
      event.preventDefault();
      commit(activeIndex);
    } else if (key === 'Tab') {
      // Never prevented: park focus on the return target first, so the
      // browser's own Tab / Shift+Tab carries on from the trigger instead of
      // from an element that is about to unmount.
      close(false);
      if (!editable && document.activeElement !== returnFocusRef.current) returnFocusRef.current?.focus();
    }
  };

  const onEscape = useEffectEvent(() => close(true));
  const onDismiss = useEffectEvent(() => close(false));

  // Escape: the open popup is the topmost layer (lib/escapeStack.ts).
  useEffect(() => {
    if (!open) return;
    return pushEscapeLayer(() => {
      onEscape();
    });
  }, [open]);

  // Dismiss when focus leaves the widget or the pointer goes down outside it.
  useEffect(() => {
    const root = rootRef.current;
    if (!open || !root) return;
    const onFocusOut = (event: FocusEvent) => {
      const next = event.relatedTarget;
      if (next instanceof Node && root.contains(next)) return;
      onDismiss();
    };
    const onMouseDown = (event: MouseEvent) => {
      if (event.target instanceof Node && root.contains(event.target)) return;
      onDismiss();
    };
    root.addEventListener('focusout', onFocusOut);
    window.addEventListener('mousedown', onMouseDown);
    return () => {
      root.removeEventListener('focusout', onFocusOut);
      window.removeEventListener('mousedown', onMouseDown);
    };
  }, [open, rootRef]);

  // Keep the active option in view inside a scrolling popup.
  useEffect(() => {
    if (!open || activeIndex < 0) return;
    const option = document.getElementById(`${listboxId}-option-${activeIndex}`);
    if (option && typeof option.scrollIntoView === 'function') option.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, listboxId, open]);

  useEffect(() => () => window.clearTimeout(bufferTimerRef.current), []);

  return {
    listboxId,
    optionId,
    activeIndex,
    activeDescendant,
    setActiveIndex,
    openAt,
    close,
    onKeyDown,
  };
}
