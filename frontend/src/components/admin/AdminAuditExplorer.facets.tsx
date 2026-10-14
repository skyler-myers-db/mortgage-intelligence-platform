/**
 * The audit explorer's facet-fed menus (W5d, tables-10;
 * deviation:filter-menu-facet-counts, deviation:audit-explorer-controls).
 *
 * GET /audit/facets is a RECORDED read (one VIEW_AUDIT_LEDGER 'facets' row
 * per served request), so it starts ONLY when the Action or Event type menu
 * reports an explicit open (FilterSelect -> useFilterFacetCounts), for the
 * applied since / until window. Never on mount, hover, focus, a poll, a
 * prefetch or a filter change: a window change disarms the read until the
 * next open, a closed menu leaves the query disabled (so a window focus
 * never refetches it), and a reopen within five minutes is served from the
 * cache. A failed read shows FilterSelect's 'Counts unavailable'.
 *
 * The Action menu's options come from the facets (most frequent first); the
 * Event type options stay the labels registry, with counts beside them.
 */
import { createContext, useContext, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FilterFacetContext, type FilterFacetCounts, type FilterFacetSource } from '../ui/FilterSelect.facets';
import { auditFacets, type AuditFacetsResponse, type AuditFacetValue } from '../../lib/apiClients/auditExplorer';
import { queryKeys } from '../../lib/queryKeys';
import { eventTypeCodeForOption } from './AdminAuditExplorer.labels';
import { AUDIT_FILTER_PARAMS, auditDayBoundary, parseAuditExplorerFilters } from './AdminAuditExplorer.params';

export const ACTION_FILTER_LABEL = 'Action';
export const EVENT_TYPE_FILTER_LABEL = 'Event type';
export const ALL_ACTIONS_OPTION = 'All actions';
export const AUDIT_FACET_STALE_MS = 5 * 60_000;

const FACET_FOR_LABEL: Readonly<Record<string, 'actions' | 'event_types'>> = {
  [ACTION_FILTER_LABEL]: 'actions',
  [EVENT_TYPE_FILTER_LABEL]: 'event_types',
};

/** An action the explorer's URL parser accepts (its own pattern, not a copy). */
function isAcceptedAction(value: string): boolean {
  return parseAuditExplorerFilters(new URLSearchParams([[AUDIT_FILTER_PARAMS.action, value]])).action === value;
}

/**
 * The Action menu: 'All actions', then the facet actions most frequent first
 * (values the URL would refuse dropped), plus the applied action when the
 * facet window lacks it, so a shared link still shows what it filters on.
 */
export function actionFilterOptions(facets: readonly AuditFacetValue[] | null, appliedAction: string): string[] {
  const actions = [...(facets ?? [])]
    .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value))
    .map((facet) => facet.value)
    .filter(isAcceptedAction);
  if (appliedAction && !actions.includes(appliedAction)) actions.push(appliedAction);
  return [ALL_ACTIONS_OPTION, ...actions];
}

const ActionFacetsContext = createContext<readonly AuditFacetValue[] | null>(null);

/** The facet actions read so far (null before the first explicit open). */
export function useAuditActionFacets(): readonly AuditFacetValue[] | null {
  return useContext(ActionFacetsContext);
}

function facetCount(data: AuditFacetsResponse, facet: 'actions' | 'event_types', option: string, appliedEventType: string): number | null {
  if (facet === 'actions') {
    if (option === ALL_ACTIONS_OPTION) return null;
    return data.actions.find((value) => value.value === option)?.count ?? 0;
  }
  const code = eventTypeCodeForOption(option, appliedEventType || null);
  if (code === null) return null;
  return data.event_types.find((value) => value.value === code)?.count ?? 0;
}

interface AuditFacetsProviderProps {
  /** The applied window, as `YYYY-MM-DD` days ('' = open). */
  since: string;
  until: string;
  /** The applied event type, so the menu's appended unknown code can be counted. */
  eventType: string;
  children: ReactNode;
}

export function AuditFacetsProvider({ since, until, eventType, children }: AuditFacetsProviderProps) {
  const facetWindow = {
    since: since ? auditDayBoundary(since, false) : null,
    until: until ? auditDayBoundary(until, true) : null,
  };
  const windowKey = `${facetWindow.since ?? ''}|${facetWindow.until ?? ''}`;
  // Which counted menu is open, and the window it was opened under.
  const [opened, setOpened] = useState<{ label: string; windowKey: string } | null>(null);
  const openNow = opened !== null && opened.windowKey === windowKey;
  const query = useQuery({
    queryKey: queryKeys.auditFacets(windowKey),
    queryFn: ({ signal }) => auditFacets(facetWindow, signal),
    enabled: openNow,
    staleTime: AUDIT_FACET_STALE_MS,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const source: FilterFacetSource = {
    setOpen: (label, open) => {
      if (!(label in FACET_FOR_LABEL)) return;
      setOpened((current) => (open ? { label, windowKey } : current?.label === label ? null : current));
    },
    counts: (label): FilterFacetCounts | null => {
      const facet = FACET_FOR_LABEL[label];
      if (!facet || !openNow || opened?.label !== label) return null;
      const data = query.data;
      if (query.isError && !data) return { status: 'error', countFor: () => null };
      if (!data) return { status: 'loading', countFor: () => null };
      return { status: 'ready', countFor: (option) => facetCount(data, facet, option, eventType) };
    },
  };
  return (
    <FilterFacetContext value={source}>
      <ActionFacetsContext value={query.data?.actions ?? null}>{children}</ActionFacetsContext>
    </FilterFacetContext>
  );
}
