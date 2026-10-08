import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { useNavigate } from 'react-router';
import { useApp } from '../AppContext';
import { Icon, type IconName } from '../Icon';
import { useBorrowerSearch } from '../../hooks/useBorrowerSearch';
import { openGenie } from '../../lib/genieOpen';
import { openShortcutOverlay } from '../../lib/keymap';
import { saveDataRequested } from '../../lib/prefetch';
import { preloadRouteForPath } from '../../lib/routePreloaders';
import { useAuditLedgerAccess } from '../../lib/sessionQuery';
import type { LeadSummary } from '../../types';
import { useModalDialog } from '../../hooks/useModalDialog';
import {
  commandActionsForAccess,
  commandVerbActions,
  filterCommandActions,
  type CommandAction,
} from './commandActions';
import { dedupePaletteBorrowers, type PaletteSourceRow } from './commandPaletteSources';
import { currentCommandSelection, useCommandSelection } from './commandSelection';
import type { CommandPaletteDialogProps } from './CommandPalette';
import { useCommandPaletteSources, useLiveShortcut, type LiveShortcut } from './useCommandPaletteSources';

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
 * This module is the palette's lazy DIALOG (audit 2026-09-21 `bundle-04`):
 * CommandPalette.tsx is the shell host that owns `open`, the ⌘K binding and
 * the preloads, and mounts this once its chunk has loaded.
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
 *
 * Provider groups (audit shell-07 items 2 and 5, tables-09 phase 2;
 * deviation:palette-provider-groups): Saved views, Segments, Glossary, Saved
 * leads and Recent borrowers sit between Pages & actions and the live
 * Borrowers, and an administrator's Data assets follow them; Ask Genie stays
 * last. Their reads are audit-free and open-gated (useCommandPaletteSources),
 * and every row navigates. A row whose action has a live keymap binding shows
 * its keycaps and carries aria-keyshortcuts (item 3). The groups reuse the
 * prototype `.cmdk__group` / `.cmdk__row` BEM; no new class.
 */

type FlatItem =
  | { kind: 'action'; action: CommandAction }
  | { kind: 'source'; row: PaletteSourceRow }
  | { kind: 'genie'; prompt: string }
  | { kind: 'borrower'; lead: LeadSummary };

/** One rendered group: its accessible name, visible label and rows, in list order. */
interface PaletteSection {
  key: string;
  ariaLabel: string;
  label: string;
  items: FlatItem[];
}

/**
 * The route chunk the row opens, for the active-row preload (audit bundle-09
 * item 2): a route action's path, or the dossier route for a borrower row
 * (preloadRouteForPath maps it to the borrower-360 chunk; no request carries
 * the id). Verb, workspace-command and Ask Genie rows open no route.
 */
function routeChunkPath(item: FlatItem | undefined): string | null {
  if (item?.kind === 'borrower') return `/borrower-360/${item.lead.borrower_id}`;
  if (item?.kind === 'source') return item.row.to;
  if (item?.kind === 'action' && item.action.target.kind === 'route') return item.action.target.to;
  return null;
}

function actionItems(actions: readonly CommandAction[]): FlatItem[] {
  return actions.map((action) => ({ kind: 'action', action }));
}

const MAX_BORROWERS = 6;
/** Typed text this long or longer also offers "Ask Genie: <text>". */
const MIN_GENIE_QUERY = 2;

export function CommandPaletteDialog({ open, onClose: close }: CommandPaletteDialogProps) {
  const navigate = useNavigate();
  const {
    theme,
    setTheme,
    consoleOpen,
    setConsoleOpen,
    setGenieOpen,
    canAccessAdmin,
  } = useApp();
  // The ledger entry for administrators AND read-only auditors: the same
  // fail-closed session decision RouteNav and the Rail use (D-audit-reads-c3).
  const canReadAudit = useAuditLedgerAccess();
  const [query, setQuery] = useState('');
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
      setActiveIndex(0);
    }
  }
  // A new open never keeps the previous session's choice of the Genie row.
  useLayoutEffect(() => {
    if (open) onGenieRowRef.current = false;
  }, [open]);

  // The full-viewport layer IS the backdrop ('self'): a press on it outside
  // the panel closes; a press inside the panel never does.
  useModalDialog({ open, dialogRef, initialFocusRef: inputRef, onDismiss: close, backdrop: 'self' });

  // The one shared borrower / geography search (hooks/useBorrowerSearch,
  // runtime-06): the topbar's cache entry for the same term answers here
  // without a request. Actions are local; the network only adds borrowers.
  const borrowerSearch = useBorrowerSearch(query, { enabled: open });
  const searchStatus = borrowerSearch.status;
  const sources = useCommandPaletteSources(open, query);
  // A borrower another group lists (a saved lead, a recent) appears there only.
  const borrowers = useMemo(
    () => dedupePaletteBorrowers(borrowerSearch.rows, sources.groups).slice(0, MAX_BORROWERS),
    [borrowerSearch.rows, sources.groups],
  );
  const liveShortcut = useLiveShortcut();

  const actions = useMemo(
    () => filterCommandActions(query, commandActionsForAccess(canAccessAdmin, undefined, canReadAudit)),
    [canAccessAdmin, canReadAudit, query],
  );
  // Verbs on the page's published selection come first ("Approve 12
  // selected…"); they exist only while a page has a selection.
  const selection = useCommandSelection();
  const verbs = useMemo(
    () => filterCommandActions(query, commandVerbActions(selection)),
    [selection, query],
  );
  const genieQuery = query.trim();
  const { sections, sectionStarts, statusSlot } = useMemo(() => {
    const sourceSection = (group: { id: string; label: string; rows: readonly PaletteSourceRow[] }): PaletteSection => ({
      key: group.id,
      ariaLabel: group.label,
      label: group.label,
      items: group.rows.map((row) => ({ kind: 'source', row })),
    });
    const all: PaletteSection[] = [
      { key: 'verbs', ariaLabel: 'Selected borrowers', label: 'Selected borrowers', items: actionItems(verbs) },
      { key: 'actions', ariaLabel: 'Pages and actions', label: 'Pages & actions', items: actionItems(actions) },
      ...sources.groups.map(sourceSection),
      {
        key: 'borrowers',
        ariaLabel: 'Borrowers',
        label: 'Borrowers',
        items: borrowers.map((lead) => ({ kind: 'borrower', lead })),
      },
      ...(sources.assetGroup ? [sourceSection(sources.assetGroup)] : []),
      // Last: Enter at the first row must reach a match before the fallback.
      {
        key: 'genie',
        ariaLabel: 'Ask Genie',
        label: 'Ask Genie',
        items: genieQuery.length >= MIN_GENIE_QUERY ? [{ kind: 'genie', prompt: genieQuery }] : [],
      },
    ];
    const shown = (section: PaletteSection) => section.items.length > 0;
    const kept = all.filter(shown);
    const borrowersAt = all.findIndex((section) => section.key === 'borrowers');
    return {
      sections: kept,
      // Each group's first row index in `items` order (the option ids).
      sectionStarts: kept.map((_, at) => kept.slice(0, at).reduce((sum, section) => sum + section.items.length, 0)),
      // The borrower search's status line sits at the Borrowers group's
      // place (after it when it has rows), before Data assets and Genie.
      statusSlot: all.slice(0, borrowersAt + 1).filter(shown).length,
    };
  }, [verbs, actions, sources.groups, sources.assetGroup, borrowers, genieQuery]);
  const items: FlatItem[] = useMemo(() => sections.flatMap((section) => section.items), [sections]);

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
      if (item.kind === 'source') {
        close();
        navigate(item.row.to);
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
      // The sheet is a modal of its own: open it once the palette has closed.
      else if (target.command === 'open-shortcuts') window.requestAnimationFrame(() => openShortcutOverlay());
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

  // Warm the active row's route chunk while the palette is open (bundle-09
  // item 2), so Enter does not wait on a cold download. Chunks only: no
  // query, no prefetchRouteData and no audited read; nothing under Save-Data.
  useEffect(() => {
    if (!open || saveDataRequested()) return;
    const path = routeChunkPath(items[activeIndex]);
    if (path) preloadRouteForPath(path);
  }, [open, activeIndex, items]);

  // Keep the active row scrolled into view as arrows move it.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-cmd-index="${activeIndex}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, open]);

  const optionId = (i: number) => `cmdk-option-${i}`;
  const matchCount = items.filter((it) => it.kind !== 'genie').length;
  const borrowerCount = items.filter((it) => it.kind === 'borrower').length;
  // At most one line speaks (shell-07 item 4): the empty state only once the
  // borrower search has neither failed nor is still running.
  const searching = query.trim().length >= 2 && searchStatus === 'loading' && borrowerCount === 0;
  const searchFailed = query.trim().length >= 2 && searchStatus === 'error';
  const noMatch = matchCount === 0 && searchStatus !== 'loading' && searchStatus !== 'error';
  const statusText = searching
    ? 'Searching borrowers…'
    : searchFailed
      ? 'Borrower search is temporarily unavailable.'
      : noMatch ? `No pages, actions, or borrowers match “${query.trim()}”.` : '';
  const searchStatusLine = searching || searchFailed
    ? <div className={searchFailed ? 'cmdk__status cmdk__status--error' : 'cmdk__status'} aria-hidden="true">{statusText}</div>
    : null;
  const rowFor = (item: FlatItem, i: number) => {
    const common = {
      index: i,
      optionId: optionId(i),
      active: i === activeIndex,
      onActivate: () => runItem(item),
      onHover: () => moveTo(i),
    };
    if (item.kind === 'borrower') {
      const { lead } = item;
      return (
        <CommandRow key={lead.borrower_id} {...common} icon="user" label={lead.borrower_id}
          hint={`${lead.city}, ${lead.state} · ${lead.zip}`} mono />
      );
    }
    if (item.kind === 'source') {
      const { row } = item;
      return <CommandRow key={row.id} {...common} icon={row.icon} label={row.label} hint={row.hint} mono={Boolean(row.borrowerId)} />;
    }
    if (item.kind === 'genie') {
      return (
        <CommandRow key="ask-genie" {...common} icon="sparkle" label={`Ask Genie: ${item.prompt}`}
          hint="Opens Genie with this question; you press Ask" />
      );
    }
    const { action } = item;
    return (
      <CommandRow key={action.id} {...common} icon={action.icon} label={action.label} hint={action.hint}
        shortcut={liveShortcut(action.shortcut)} />
    );
  };

  return (
    <dialog
      ref={dialogRef}
      className="cmdk"
      data-rum-target="palette"
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
              says so when nothing else matched. While the borrower search
              runs or after it failed, its status line shows instead (shell-07
              item 4): "no borrowers" is not known yet. The visible lines are
              aria-hidden: a listbox may own only options and groups, so the
              one live region below the list announces them. */}
          {noMatch && (
            <div className="cmdk__empty" aria-hidden="true">{statusText}</div>
          )}

          {/* Every group in `items` order, each row at its index there; the
              borrower search's status line follows the Borrowers group's
              place, before Data assets and the Genie fallback. */}
          {sections.map((section, index) => (
            <Fragment key={section.key}>
              {index === statusSlot && searchStatusLine}
              <div className="cmdk__group" role="group" aria-label={section.ariaLabel}>
                <div className="cmdk__group-label">{section.label}</div>
                {section.items.map((item, at) => rowFor(item, sectionStarts[index] + at))}
              </div>
            </Fragment>
          ))}
          {statusSlot >= sections.length && searchStatusLine}
        </div>
        {/* The palette's one live region, outside the listbox (see above). */}
        <div className="sr-only" role="status">{open ? statusText : ''}</div>

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
  shortcut = null,
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
  /** The action's live keymap chord (useLiveShortcut), or null. */
  shortcut?: LiveShortcut | null;
  onActivate: () => void;
  onHover: () => void;
}) {
  return (
    <button
      type="button"
      id={optionId}
      role="option"
      aria-selected={active}
      aria-keyshortcuts={shortcut?.aria}
      data-cmd-index={index}
      className={`cmdk__row${active ? ' is-active' : ''}`}
      // Keep focus in the input (where arrow keys work); select on hover.
      onMouseDown={(e) => e.preventDefault()}
      onMouseEnter={onHover}
      onClick={onActivate}
    >
      <Icon name={icon} size={14} className="cmdk__row-icon" />
      <span className={`cmdk__row-label${mono ? ' mono' : ''}`}>{label}</span>
      <span className="cmdk__row-hint">
        {hint}
        {shortcut?.keycaps.map((cap) => <kbd key={cap}>{cap}</kbd>)}
      </span>
    </button>
  );
}
