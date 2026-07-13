import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { useNavigate } from 'react-router-dom';
import { useApp } from '../AppContext';
import { Icon, type IconName } from '../Icon';
import { api } from '../../lib/api';
import type { LeadSummary } from '../../types';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import {
  commandActionsForAccess,
  filterCommandActions,
  type CommandAction,
} from './commandActions';

/**
 * ⌘K command palette (re-audit #4 Buyer-Wow #1). Reuses the wired
 * /api/borrowers/search and a static action registry to give the demo a
 * keyboard-first "fly through the app" moment. Deterministic: actions are
 * local; only the borrower/geography rows hit the network (debounced,
 * abortable). Fully accessible — combobox + listbox with
 * aria-activedescendant, focus trap, Esc to close, restore focus on close —
 * and reduced-motion aware (CSS).
 *
 * Open with ⌘K (mac) / Ctrl+K. The existing "/" topbar-search shortcut is
 * untouched; this is the heavier cross-surface launcher.
 */

type FlatItem =
  | { kind: 'action'; action: CommandAction }
  | { kind: 'borrower'; lead: LeadSummary };

const DEBOUNCE_MS = 160;
const MAX_BORROWERS = 6;

export function CommandPalette() {
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

  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const resetTransient = useCallback(() => {
    setQuery('');
    setBorrowers([]);
    setSearchStatus('idle');
    setActiveIndex(0);
  }, []);
  const close = useCallback(() => {
    setOpen(false);
    resetTransient();
  }, [resetTransient]);
  const openPalette = useCallback(() => {
    // Reset on OPEN too, so a ⌘K toggle-open never flashes the previous
    // query/results (frontend signoff: the old toggle bypassed teardown).
    resetTransient();
    setOpen(true);
  }, [resetTransient]);

  // The global listener is bound once, so it can't read `open` from a stale
  // closure — track the latest value in a ref (updated in an effect, never
  // during render).
  const openRef = useRef(open);
  useEffect(() => {
    openRef.current = open;
  }, [open]);

  // Global ⌘K / Ctrl+K toggle, with symmetric teardown on both edges.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        if (openRef.current) close();
        else openPalette();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [close, openPalette]);

  useFocusTrap({ open, containerRef, initialFocusRef: inputRef, onClose: close });

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
  const items: FlatItem[] = useMemo(
    () => [
      ...actions.map((action) => ({ kind: 'action' as const, action })),
      ...borrowers.map((lead) => ({ kind: 'borrower' as const, lead })),
    ],
    [actions, borrowers],
  );

  // Clamp the active index whenever the result set shrinks.
  useEffect(() => {
    setActiveIndex((i) => (items.length === 0 ? 0 : Math.min(i, items.length - 1)));
  }, [items.length]);

  const runItem = useCallback(
    (item: FlatItem) => {
      if (item.kind === 'borrower') {
        close();
        navigate(`/borrower-360/${item.lead.borrower_id}`);
        return;
      }
      const { target } = item.action;
      if (target.kind === 'route') {
        close();
        navigate(target.to);
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
        setActiveIndex((i) => (items.length === 0 ? 0 : (i + 1) % items.length));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex((i) => (items.length === 0 ? 0 : (i - 1 + items.length) % items.length));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const item = items[activeIndex];
        if (item) runItem(item);
      }
      // Esc is handled by the focus trap.
    },
    [items, activeIndex, runItem],
  );

  // Keep the active row scrolled into view as arrows move it.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-cmd-index="${activeIndex}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, open]);

  if (!open) return null;

  const optionId = (i: number) => `cmdk-option-${i}`;
  let runningIndex = -1;
  const actionItems = items.filter((it) => it.kind === 'action') as Extract<FlatItem, { kind: 'action' }>[];
  const borrowerItems = items.filter((it) => it.kind === 'borrower') as Extract<FlatItem, { kind: 'borrower' }>[];

  return (
    <div
      className="cmdk"
      role="presentation"
      onMouseDown={(e) => {
        // Backdrop click closes; clicks inside the panel don't.
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        className="cmdk__panel"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        ref={containerRef}
      >
        <div className="cmdk__search">
          <Icon name="search" size={14} />
          <input
            ref={inputRef}
            className="cmdk__input"
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls="cmdk-listbox"
            aria-activedescendant={items.length > 0 ? optionId(activeIndex) : undefined}
            aria-label="Search borrowers, ZIPs, pages, and actions"
            placeholder="Search borrowers, ZIPs, pages, actions…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActiveIndex(0);
            }}
            onKeyDown={onInputKeyDown}
          />
          <kbd className="cmdk__hint">esc</kbd>
        </div>

        <div className="cmdk__list" id="cmdk-listbox" role="listbox" ref={listRef}>
          {items.length === 0 && (
            <div className="cmdk__empty" role="status">
              No pages, actions, or borrowers match “{query.trim()}”.
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
                    onHover={() => setActiveIndex(i)}
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
                    onHover={() => setActiveIndex(i)}
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
        </div>

        <div className="cmdk__footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>esc</kbd> close</span>
        </div>
      </div>
    </div>
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
