import { useCallback, useRef, type KeyboardEvent } from 'react';
import { useSearchParams } from 'react-router';

/**
 * Page tabs for `/ask-genie` (audit 2026-09-21 `visual-07` / `genie-09`):
 * the route opens on the conversation, and the Growth Agent workflows and
 * saved monitors each get their own tab.
 *
 * The selected tab lives in the URL (`?tab=workflows`, `?tab=monitors`; Ask is
 * the bare path), so a deep link opens the right tab and Back returns to the
 * previous one. Selecting a tab pushes a history entry on the same pathname,
 * which the shell's useMainScroll treats as a filter change (offset kept).
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

/** The selected tab, read from and written to the URL. */
export function useAskGenieTab(): [AskGenieTab, (next: AskGenieTab) => void] {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = parseAskGenieTab(searchParams.get(ASK_GENIE_TAB_PARAM));
  const selectTab = useCallback(
    (next: AskGenieTab) => {
      if (next === tab) return;
      setSearchParams((current) => {
        const params = new URLSearchParams(current);
        if (next === 'ask') params.delete(ASK_GENIE_TAB_PARAM);
        else params.set(ASK_GENIE_TAB_PARAM, next);
        return params;
      });
    },
    [tab, setSearchParams],
  );
  return [tab, selectTab];
}

interface AskGenieTabsProps {
  tab: AskGenieTab;
  onSelect: (next: AskGenieTab) => void;
}

export function AskGenieTabs({ tab, onSelect }: AskGenieTabsProps) {
  const refs = useRef<Partial<Record<AskGenieTab, HTMLButtonElement | null>>>({});
  const ids = ASK_GENIE_TABS.map((item) => item.id);

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = ids.indexOf(tab);
    let next: AskGenieTab | null = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = ids[(index + 1) % ids.length];
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = ids[(index - 1 + ids.length) % ids.length];
    else if (event.key === 'Home') next = ids[0];
    else if (event.key === 'End') next = ids[ids.length - 1];
    if (!next) return;
    event.preventDefault();
    onSelect(next);
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
            onClick={() => onSelect(item.id)}
            onKeyDown={onKeyDown}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
