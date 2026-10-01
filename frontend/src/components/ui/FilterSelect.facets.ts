/**
 * Optional option counts for a FilterSelect menu (audit tables-06). A route
 * that can count its menus mounts a provider (the Lead Queue's
 * FilterFacetsProvider, routes/lead-queue.facets.tsx); with none, every
 * FilterSelect renders exactly as before, so Analytics, Segment
 * Intelligence, Portfolio Builder and Admin are unchanged.
 *
 * The provider owns the read. A menu only REPORTS that it opened or closed
 * (so the count is fetched on an explicit open, never on hover or mount) and
 * reads back the counts for its label. Kept free of the provider's query and
 * API client so FilterSelect's shared chunk does not grow with them.
 * deviation:filter-menu-facet-counts
 */
import { createContext, useContext, useEffect } from 'react';

export interface FilterFacetCounts {
  status: 'loading' | 'ready' | 'error';
  /** The count to show beside `option`, or null when that option has none. */
  countFor: (option: string) => number | null;
}

export interface FilterFacetSource {
  /** A labelled menu opened (true) or closed (false). */
  setOpen: (label: string, open: boolean) => void;
  /** The counts for a labelled menu, or null when the label is not counted. */
  counts: (label: string) => FilterFacetCounts | null;
}

export const FilterFacetContext = createContext<FilterFacetSource | null>(null);

/**
 * The counts for the menu `label` while it is `open`, else null. Reports the
 * open state to the provider; with no provider it does nothing at all.
 */
export function useFilterFacetCounts(label: string, open: boolean): FilterFacetCounts | null {
  const source = useContext(FilterFacetContext);
  useEffect(() => {
    if (!source || !open) return undefined;
    source.setOpen(label, true);
    return () => source.setOpen(label, false);
  }, [source, label, open]);
  return source && open ? source.counts(label) : null;
}
