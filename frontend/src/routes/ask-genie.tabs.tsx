import { useCallback, useLayoutEffect, useRef, type KeyboardEvent } from 'react';
import { useLocation, useNavigate, useSearchParams, type NavigateOptions } from 'react-router';

/**
 * Page tabs for `/ask-genie` (audit 2026-09-21 `visual-07` / `genie-09`):
 * the route opens on the conversation, and the Growth Agent workflows and
 * saved monitors each get their own tab.
 *
 * The selected tab lives in the URL (`?tab=workflows`, `?tab=monitors`; Ask is
 * the bare path), so a deep link opens the right tab and Back returns to the
 * previous one. Selecting a tab pushes a history entry on the same pathname,
 * which the shell's useMainScroll treats as a filter change (offset kept).
 * The arrow keys select as they move (automatic activation), so one keyboard
 * pass over the tabs is ONE history entry, not one per key press: the first
 * key press pushes, later ones replace it, and arrowing back to the tab the
 * pass started on returns to that entry. Back then undoes the whole pass.
 *
 * The router renders a new location in a transition, so a second key can
 * land before the first key's tab has rendered. Each key therefore moves
 * from the tab it was pressed on (the focused tab), and the hook keeps the
 * selection it last wrote (tab and pass origin) in a ref that every commit of
 * a new location re-syncs, instead of reading the last rendered URL: with
 * the rendered URL, End then Home pressed quickly pushed a second entry and
 * Home's Back step landed on the wrong tab.
 *
 * Markup is the prototype's `.layout-tabs` tablist (design_files/Module 0
 * Prototype.html:958-960, placed in the hero's right slot as at :2148-2152)
 * with the EvidenceDrawer's roving-tabindex keyboard model: Left / Right
 * (and Up / Down) move and select, Home / End jump.
 *
 * TODO(audit 2026-09-21 filter-listbox lane): converge on the shared Tabs
 * primitive once it lands; this is a local tablist on purpose until then.
 */

export type AskGenieTab = 'ask' | 'workflows' | 'monitors';

export const ASK_GENIE_TABS: ReadonlyArray<{ id: AskGenieTab; label: string }> = [
  { id: 'ask', label: 'Ask' },
  { id: 'workflows', label: 'Workflows' },
  { id: 'monitors', label: 'Saved monitors' },
];

export const ASK_GENIE_TAB_PARAM = 'tab';

export function parseAskGenieTab(value: string | null): AskGenieTab {
  return value === 'workflows' || value === 'monitors' ? value : 'ask';
}

export function askGenieTabId(tab: AskGenieTab): string {
  return `ask-genie-tab-${tab}`;
}

export function askGeniePanelId(tab: AskGenieTab): string {
  return `ask-genie-panel-${tab}`;
}

/** How a tab was chosen: a click (or a link-like action) or the arrow keys. */
export type AskGenieTabInput = 'pointer' | 'keyboard';

/** History-state key on an entry a keyboard pass wrote: the tab it began on. */
const KEYBOARD_PASS_ORIGIN = 'askGenieTabPassOrigin';

function keyboardPassOrigin(state: unknown): AskGenieTab | null {
  if (typeof state !== 'object' || state === null) return null;
  const origin = (state as Record<string, unknown>)[KEYBOARD_PASS_ORIGIN];
  return origin === 'ask' || origin === 'workflows' || origin === 'monitors' ? origin : null;
}

/** The selected tab, read from and written to the URL. */
export function useAskGenieTab(): [AskGenieTab, (next: AskGenieTab, input?: AskGenieTabInput) => void] {
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const tab = parseAskGenieTab(searchParams.get(ASK_GENIE_TAB_PARAM));
  // Set only on an entry a keyboard pass wrote; that entry was pushed from
  // the pass's origin, so the origin is always the entry right before it.
  const passOrigin = keyboardPassOrigin(location.state);
  // The selection this hook last wrote, ahead of the router's render of it.
  const latestRef = useRef({ tab, passOrigin });
  useLayoutEffect(() => {
    latestRef.current = { tab, passOrigin };
  }, [location.key, tab, passOrigin]);
  const selectTab = useCallback(
    (next: AskGenieTab, input: AskGenieTabInput = 'pointer') => {
      const latest = latestRef.current;
      if (next === latest.tab) return;
      const write = (options?: NavigateOptions) =>
        setSearchParams((current) => {
          const params = new URLSearchParams(current);
          if (next === 'ask') params.delete(ASK_GENIE_TAB_PARAM);
          else params.set(ASK_GENIE_TAB_PARAM, next);
          return params;
        }, options);
      if (input === 'keyboard' && latest.passOrigin !== null) {
        const origin = latest.passOrigin;
        // Back to the entry the pass was pushed from, which carries no pass.
        if (next === origin) {
          latestRef.current = { tab: next, passOrigin: null };
          navigate(-1);
          return;
        }
        latestRef.current = { tab: next, passOrigin: origin };
        write({ replace: true, state: { [KEYBOARD_PASS_ORIGIN]: origin } });
        return;
      }
      if (input === 'keyboard') {
        latestRef.current = { tab: next, passOrigin: latest.tab };
        write({ state: { [KEYBOARD_PASS_ORIGIN]: latest.tab } });
        return;
      }
      latestRef.current = { tab: next, passOrigin: null };
      write();
    },
    [setSearchParams, navigate],
  );
  return [tab, selectTab];
}

interface AskGenieTabsProps {
  tab: AskGenieTab;
  onSelect: (next: AskGenieTab, input: AskGenieTabInput) => void;
}

export function AskGenieTabs({ tab, onSelect }: AskGenieTabsProps) {
  const refs = useRef<Partial<Record<AskGenieTab, HTMLButtonElement | null>>>({});
  const ids = ASK_GENIE_TABS.map((item) => item.id);

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    // The tab the key was pressed on: focus moves at once, the render later.
    const pressedOn = ids.find((id) => askGenieTabId(id) === event.currentTarget.id) ?? tab;
    const index = ids.indexOf(pressedOn);
    let next: AskGenieTab | null = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = ids[(index + 1) % ids.length];
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = ids[(index - 1 + ids.length) % ids.length];
    else if (event.key === 'Home') next = ids[0];
    else if (event.key === 'End') next = ids[ids.length - 1];
    if (!next) return;
    event.preventDefault();
    onSelect(next, 'keyboard');
    refs.current[next]?.focus();
  };

  return (
    <div className="layout-tabs" role="tablist" aria-label="Ask Genie views">
      {ASK_GENIE_TABS.map((item) => {
        const selected = item.id === tab;
        return (
          <button
            key={item.id}
            ref={(node) => {
              refs.current[item.id] = node;
            }}
            type="button"
            role="tab"
            id={askGenieTabId(item.id)}
            aria-selected={selected}
            aria-controls={askGeniePanelId(item.id)}
            tabIndex={selected ? 0 : -1}
            className={selected ? 'is-active' : undefined}
            onClick={() => onSelect(item.id, 'pointer')}
            onKeyDown={onKeyDown}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
