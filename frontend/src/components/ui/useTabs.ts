import { useRef, type KeyboardEvent as ReactKeyboardEvent } from 'react';

/**
 * APG tabs with automatic activation (2026-09-21 audit a11y-02 / stack-05),
 * extracted from the evidence drawer, which already had it right, so the
 * Analytics views and the borrower proof drawer stop shipping click-only
 * tabs without `aria-controls`.
 *
 *   - roving tabindex: only the selected tab is in the tab order;
 *   - ArrowLeft / ArrowRight move to the previous / next tab (wrapping),
 *     Home / End to the first / last, and the tab that receives focus is
 *     selected at once (automatic activation);
 *   - every tab names its panel (`aria-controls`) and the panel names its
 *     tab (`aria-labelledby`). Only the selected panel is rendered, which is
 *     why an unselected tab's `aria-controls` may point at no element.
 *
 * Ids are `${idBase}-tab-${id}` and `${idBase}-panel-${id}`, so callers keep
 * stable ids that tests and deep links can name.
 */

export interface TabsOptions<T extends string> {
  tabs: readonly T[];
  selected: T;
  onSelect: (tab: T) => void;
  idBase: string;
}

export interface TabProps {
  ref: (node: HTMLButtonElement | null) => void;
  id: string;
  type: 'button';
  role: 'tab';
  'aria-selected': boolean;
  'aria-controls': string;
  tabIndex: 0 | -1;
  onClick: () => void;
  onKeyDown: (event: ReactKeyboardEvent<HTMLButtonElement>) => void;
}

export interface TabPanelProps {
  id: string;
  role: 'tabpanel';
  'aria-labelledby': string;
}

export interface Tabs<T extends string> {
  tabListProps: { role: 'tablist'; 'aria-orientation': 'horizontal' };
  tabProps: (tab: T) => TabProps;
  panelProps: (tab: T) => TabPanelProps;
}

export function useTabs<T extends string>({ tabs, selected, onSelect, idBase }: TabsOptions<T>): Tabs<T> {
  const nodes = useRef(new Map<T, HTMLButtonElement>());
  const tabId = (tab: T) => `${idBase}-tab-${tab}`;
  const panelId = (tab: T) => `${idBase}-panel-${tab}`;

  const onKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const current = Math.max(0, tabs.indexOf(selected));
    let next: number;
    if (event.key === 'ArrowRight') next = (current + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (current - 1 + tabs.length) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault();
    const tab = tabs[next];
    if (tab === undefined) return;
    onSelect(tab);
    nodes.current.get(tab)?.focus();
  };

  return {
    tabListProps: { role: 'tablist', 'aria-orientation': 'horizontal' },
    tabProps: (tab) => ({
      ref: (node) => {
        if (node) nodes.current.set(tab, node);
        else nodes.current.delete(tab);
      },
      id: tabId(tab),
      type: 'button',
      role: 'tab',
      'aria-selected': tab === selected,
      'aria-controls': panelId(tab),
      tabIndex: tab === selected ? 0 : -1,
      onClick: () => onSelect(tab),
      onKeyDown,
    }),
    panelProps: (tab) => ({
      id: panelId(tab),
      role: 'tabpanel',
      'aria-labelledby': tabId(tab),
    }),
  };
}
