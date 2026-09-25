import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { useNavigate } from 'react-router';
import { useApp } from '../AppContext';
import { Icon, type IconName } from '../Icon';
import { api } from '../../lib/api';
import { openGenie } from '../../lib/genieOpen';
import { hasOpenModal, registerKeyBinding } from '../../lib/keymap';
import type { LeadSummary } from '../../types';
import { useModalDialog } from '../../hooks/useModalDialog';
import {
  commandActionsForAccess,
  commandVerbActions,
  filterCommandActions,
  type CommandAction,
} from './commandActions';
import { currentCommandSelection, useCommandSelection } from './commandSelection';
import { ShortcutOverlayHost } from './ShortcutOverlayHost';

/**
 * ⌘K command palette (re-audit #4 Buyer-Wow #1). Reuses the wired
 * /api/borrowers/search and a static action registry to give the demo a
 * keyboard-first "fly through the app" moment. Deterministic: actions are
 * local; only the borrower/geography rows hit the network (debounced,
 * abortable). Fully accessible — combobox + listbox with
 * aria-activedescendant, a native modal <dialog> (useModalDialog: the page
 * is inert behind it), Esc to close, restore focus on close — and
 * reduced-motion aware (CSS).
 *
 * The dialog stays mounted once rendered (audit motion-01): closing plays
 * the exit over the LAST results, which reset on the next open (the rising
 * edge of `open`), and the closing copy is inert, aria-hidden and has no
 * active descendant.
 *
 * Open with ⌘K (mac) / Ctrl+K. The existing "/" topbar-search shortcut is
 * untouched; this is the heavier cross-surface launcher.
 *
 * "Ask Genie: <typed text>" (audit 2026-09-21 `shell-07` / `genie-04`): any
 * query of two or more characters also offers a row that opens the floating
 * Genie panel with the text PREFILLED in its composer (`openGenie`). It never
 * submits and adds no endpoint: the user presses Ask, and the question takes
 * the same guarded ask path as any typed question. The row is the FALLBACK,
 * not a match: it comes after the pages, actions AND borrowers, and Enter
 * never lands on it while the borrower search is still in flight, so a
 * masked borrower id or a ZIP still opens its dossier on Enter instead of
 * being handed to Genie's composer.
 */

type FlatItem =
  | { kind: 'action'; action: CommandAction }
  | { kind: 'genie'; prompt: string }
  | { kind: 'borrower'; lead: LeadSummary };

const DEBOUNCE_MS = 160;
const MAX_BORROWERS = 6;
/** Typed text this long or longer also offers "Ask Genie: <text>". */
const MIN_GENIE_QUERY = 2;

/**
 * The shell's keyboard launchers: the ⌘K palette and the `?` shortcut sheet
 * host (the sheet itself is a lazy chunk). One mount point in AppShell.
 */
export function CommandPalette() {
  return (
    <>
      <ShortcutOverlayHost />
      <CommandPaletteSurface />
    </>
  );
}

function CommandPaletteSurface() {
  const navigate = useNavigate();
  const {
    theme,
    setTheme,
    consoleOpen,
    setConsoleOpen,
    setGenieOpen,
    canAccessAdmin,
  } = useApp();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [borrowers, setBorrowers] = useState<LeadSummary[]>([]);
  const [searchStatus, setSearchStatus] = useState<'idle' | 'loading' | 'empty' | 'error'>('idle');
  const [activeIndex, setActiveIndex] = useState(0);
  // True once the user moves onto the "Ask Genie" fallback (arrows or
  // pointer). Borrower rows land after the debounce ABOVE that row, so the
  // same index would then point at a borrower; the selection follows the row.
  const onGenieRowRef = useRef(false);

  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Transient state resets on the RISING edge of `open` (a render-time
  // latch, never an effect), so a ⌘K open never flashes the previous
  // query/results while the exit still shows them.
  const [openLatch, setOpenLatch] = useState(open);
  if (openLatch !== open) {
    setOpenLatch(open);
    if (open) {
      setQuery('');
      setBorrowers([]);
      setSearchStatus('idle');
      setActiveIndex(0);
    }
  }
  const close = useCallback(() => {
    setOpen(false);
  }, []);
  const openPalette = useCallback(() => {
    onGenieRowRef.current = false;
    setOpen(true);
  }, []);

  // The global listener is bound once, so it can't read `open` from a stale
  // closure — track the latest value in a ref (updated in an effect, never
  // during render).
  const openRef = useRef(open);
  useEffect(() => {
    openRef.current = open;
  }, [open]);

  // Global ⌘K / Ctrl+K toggle, with symmetric teardown on both edges. A
  // modifier chord in the shared keymap registry (audit wow-power-4): it
  // works while typing and stays on when single-key shortcuts are off.
  useEffect(() => registerKeyBinding({
    id: 'command-palette',
    scope: 'global',
    keys: ['Mod+K'],
    description: 'Open or close the command palette',
    allowInEditable: true,
    // Never open over ANY modal layer: a native modal dialog (the approve
    // review, "Leave without saving?") or an aria-modal one (the session
    // dialog, the evidence drawer, the ? sheet). Under a native dialog the
    // palette would mount inert and unseen and swallow the next Escape; over
    // an aria-modal layer it would fight that layer's focus trap. Closing an
    // open palette still works.
    when: () => openRef.current || !hasOpenModal(),
    run: () => {
      if (openRef.current) close();
      else openPalette();
    },
  }), [close, openPalette]);

  // The full-viewport layer IS the backdrop ('self'): a press on it outside
  // the panel closes; a press inside the panel never does.
  useModalDialog({ open, dialogRef, initialFocusRef: inputRef, onDismiss: close, backdrop: 'self' });

  // Debounced borrower/geography search (mirrors the topbar). Actions are
  // always available locally; the network only augments with borrower rows.
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (q.length < 2) {
      setBorrowers([]);
      setSearchStatus('idle');
      return;
    }
    const ctrl = new AbortController();
    setSearchStatus('loading');
    const t = window.setTimeout(() => {
      api
        .borrowerSearch(q, ctrl.signal)
        .then((rows) => {
          if (ctrl.signal.aborted) return;
          setBorrowers(rows.slice(0, MAX_BORROWERS));
          setSearchStatus(rows.length > 0 ? 'idle' : 'empty');
        })
        .catch(() => {
          if (ctrl.signal.aborted) return;
          setBorrowers([]);
          setSearchStatus('error');
        });
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(t);
      ctrl.abort();
    };
  }, [open, query]);

  const actions = useMemo(
    () => filterCommandActions(query, commandActionsForAccess(canAccessAdmin)),
    [canAccessAdmin, query],
  );
  // Verbs on the page's published selection come first ("Approve 12
  // selected…"); they exist only while a page has a selection.
  const selection = useCommandSelection();
  const verbs = useMemo(
    () => filterCommandActions(query, commandVerbActions(selection)),
    [selection, query],
  );
  const genieQuery = query.trim();
  const items: FlatItem[] = useMemo(
    () => [
      ...verbs.map((action) => ({ kind: 'action' as const, action })),
      ...actions.map((action) => ({ kind: 'action' as const, action })),
      ...borrowers.map((lead) => ({ kind: 'borrower' as const, lead })),
      // Last: Enter at the first row must reach a match before the fallback.
      ...(genieQuery.length >= MIN_GENIE_QUERY ? [{ kind: 'genie' as const, prompt: genieQuery }] : []),
    ],
    [verbs, actions, borrowers, genieQuery],
  );

  // Clamp the active index whenever the result set shrinks; keep a user who
  // chose the Genie row on it when borrower rows are inserted above it.
  useEffect(() => {
    const genieIndex = onGenieRowRef.current ? items.findIndex((item) => item.kind === 'genie') : -1;
    setActiveIndex((i) => {
      if (items.length === 0) return 0;
      return genieIndex >= 0 ? genieIndex : Math.min(i, items.length - 1);
    });
  }, [items]);

  const moveTo = useCallback(
    (index: number) => {
      onGenieRowRef.current = items[index]?.kind === 'genie';
      setActiveIndex(index);
    },
    [items],
  );

  const runItem = useCallback(
    (item: FlatItem) => {
      if (item.kind === 'borrower') {
        close();
        navigate(`/borrower-360/${item.lead.borrower_id}`);
        return;
      }
      if (item.kind === 'genie') {
        close();
        openGenie({ prompt: item.prompt });
        return;
      }
      const { target } = item.action;
      if (target.kind === 'route') {
        close();
        navigate(target.to);
        return;
      }
      if (target.kind === 'verb') {
        // The page's own guarded handler, read fresh at run time.
        close();
        currentCommandSelection()?.run(target.verb);
        return;
      }
      // Workspace commands keep the palette's close semantics consistent.
      close();
      if (target.command === 'toggle-theme') setTheme(theme === 'dark' ? 'light' : 'dark');
      else if (target.command === 'toggle-console') setConsoleOpen(!consoleOpen);
      else if (target.command === 'open-genie') setGenieOpen(true);
    },
    [close, navigate, setTheme, theme, setConsoleOpen, consoleOpen, setGenieOpen],
  );

  const onInputKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        moveTo(items.length === 0 ? 0 : (activeIndex + 1) % items.length);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        moveTo(items.length === 0 ? 0 : (activeIndex - 1 + items.length) % items.length);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const item = items[activeIndex];
        if (!item) return;
        // The borrower rows have not landed yet, so the Genie fallback only
        // SEEMS to be the first match. Hold Enter (a click still works):
        // once the search settles, Enter opens the first borrower.
        if (item.kind === 'genie' && searchStatus === 'loading' && borrowers.length === 0) return;
        runItem(item);
      }
      // Esc is handled by the focus trap.
    },
    [items, activeIndex, moveTo, runItem, searchStatus, borrowers.length],
  );

  // Keep the active row scrolled into view as arrows move it.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-cmd-index="${activeIndex}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, open]);

  const optionId = (i: number) => `cmdk-option-${i}`;
  let runningIndex = -1;
  const allActionItems = items.filter((it) => it.kind === 'action') as Extract<FlatItem, { kind: 'action' }>[];
  const verbItems = allActionItems.filter((it) => it.action.target.kind === 'verb');
  const actionItems = allActionItems.filter((it) => it.action.target.kind !== 'verb');
  const genieItems = items.filter((it) => it.kind === 'genie') as Extract<FlatItem, { kind: 'genie' }>[];
  const borrowerItems = items.filter((it) => it.kind === 'borrower') as Extract<FlatItem, { kind: 'borrower' }>[];

  return (
    <dialog
      ref={dialogRef}
      className="cmdk"
      aria-label="Command palette"
      aria-hidden={!open || undefined}
      inert={!open}
    >
      <div className="cmdk__panel">
        <div className="cmdk__search">
          <Icon name="search" size={14} />
          <input
            ref={inputRef}
            className="cmdk__input"
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls="cmdk-listbox"
            aria-activedescendant={open && items.length > 0 ? optionId(activeIndex) : undefined}
            aria-label="Search borrowers, ZIPs, pages, and actions"
            placeholder="Search borrowers, ZIPs, pages, actions…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActiveIndex(0);
              onGenieRowRef.current = false;
            }}
            onKeyDown={onInputKeyDown}
          />
          <kbd className="cmdk__hint">esc</kbd>
        </div>

        <div className="cmdk__list" id="cmdk-listbox" role="listbox" ref={listRef}>
          {/* The Genie row is a way out, not a match: the empty state still
              says so when no page, action or borrower matched. */}
          {allActionItems.length === 0 && borrowerItems.length === 0 && (
            <div className="cmdk__empty" role="status">
              No pages, actions, or borrowers match “{query.trim()}”.
            </div>
          )}

          {verbItems.length > 0 && (
            <div className="cmdk__group" role="group" aria-label="Selected borrowers">
              <div className="cmdk__group-label">Selected borrowers</div>
              {verbItems.map((item) => {
                runningIndex += 1;
                const i = runningIndex;
                return (
                  <CommandRow
                    key={item.action.id}
                    index={i}
                    optionId={optionId(i)}
                    active={i === activeIndex}
                    icon={item.action.icon}
                    label={item.action.label}
                    hint={item.action.hint}
                    onActivate={() => runItem(item)}
                    onHover={() => moveTo(i)}
                  />
                );
              })}
            </div>
          )}

          {actionItems.length > 0 && (
            <div className="cmdk__group" role="group" aria-label="Pages and actions">
              <div className="cmdk__group-label">Pages &amp; actions</div>
              {actionItems.map((item) => {
                runningIndex += 1;
                const i = runningIndex;
                return (
                  <CommandRow
                    key={item.action.id}
                    index={i}
                    optionId={optionId(i)}
                    active={i === activeIndex}
                    icon={item.action.icon}
                    label={item.action.label}
                    hint={item.action.hint}
                    onActivate={() => runItem(item)}
                    onHover={() => moveTo(i)}
                  />
                );
              })}
            </div>
          )}

          {borrowerItems.length > 0 && (
            <div className="cmdk__group" role="group" aria-label="Borrowers">
              <div className="cmdk__group-label">Borrowers</div>
              {borrowerItems.map((item) => {
                runningIndex += 1;
                const i = runningIndex;
                return (
                  <CommandRow
                    key={item.lead.borrower_id}
                    index={i}
                    optionId={optionId(i)}
                    active={i === activeIndex}
                    icon="user"
                    label={item.lead.borrower_id}
                    hint={`${item.lead.city}, ${item.lead.state} · ${item.lead.zip}`}
                    mono
                    onActivate={() => runItem(item)}
                    onHover={() => moveTo(i)}
                  />
                );
              })}
            </div>
          )}

          {query.trim().length >= 2 && searchStatus === 'loading' && borrowerItems.length === 0 && (
            <div className="cmdk__status" role="status">Searching borrowers…</div>
          )}
          {query.trim().length >= 2 && searchStatus === 'error' && (
            <div className="cmdk__status cmdk__status--error" role="status">Borrower search is temporarily unavailable.</div>
          )}

          {/* The fallback, after every match (and the same order as `items`,
              so the running index stays in step). */}
          {genieItems.length > 0 && (
            <div className="cmdk__group" role="group" aria-label="Ask Genie">
              <div className="cmdk__group-label">Ask Genie</div>
              {genieItems.map((item) => {
                runningIndex += 1;
                const i = runningIndex;
                return (
                  <CommandRow
                    key="ask-genie"
                    index={i}
                    optionId={optionId(i)}
                    active={i === activeIndex}
                    icon="sparkle"
                    label={`Ask Genie: ${item.prompt}`}
                    hint="Opens Genie with this question; you press Ask"
                    onActivate={() => runItem(item)}
                    onHover={() => moveTo(i)}
                  />
                );
              })}
            </div>
          )}
        </div>

        <div className="cmdk__footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>esc</kbd> close</span>
        </div>
      </div>
    </dialog>
  );
}

function CommandRow({
  index,
  optionId,
  active,
  icon,
  label,
  hint,
  mono,
  onActivate,
  onHover,
}: {
  index: number;
  optionId: string;
  active: boolean;
  icon: IconName;
  label: string;
  hint: string;
  mono?: boolean;
  onActivate: () => void;
  onHover: () => void;
}) {
  return (
    <button
      type="button"
      id={optionId}
      role="option"
      aria-selected={active}
      data-cmd-index={index}
      className={`cmdk__row${active ? ' is-active' : ''}`}
      // Keep focus in the input (where arrow keys work); select on hover.
      onMouseDown={(e) => e.preventDefault()}
      onMouseEnter={onHover}
      onClick={onActivate}
    >
      <Icon name={icon} size={14} className="cmdk__row-icon" />
      <span className={`cmdk__row-label${mono ? ' mono' : ''}`}>{label}</span>
      <span className="cmdk__row-hint">{hint}</span>
    </button>
  );
}
