/**
 * Lead Queue filter-menu counts (audit tables-06): how many borrowers each
 * STATE, SEGMENT, PRODUCT and APPROVAL option holds under every OTHER filter.
 *
 * deviation:filter-menu-facet-counts. The prototype's `.filter-menu`
 * (design_files/index.html:822-838, Module 0 Prototype.html:1772-1778) lists
 * option labels only; a mono count now trails each counted option.
 *
 * Read discipline: one audit-free GET /api/leads/facets, started only when a
 * person opens a counted menu (FilterSelect reports its open state through
 * components/ui/FilterSelect.facets.ts), never on hover, mount or poll; a
 * reopen within 60 s is served from cache; a filter change while the menu is
 * closed reads nothing. The count is aria-hidden and wired to its option by
 * aria-describedby, so the option's accessible NAME stays its label.
 *
 * A borrower list (?borrower_ids=) is never counted: counts over named
 * borrowers would read their attributes without the VIEW_LEADS row the list
 * writes, so the server refuses them (BORROWER_LIST_LIST_ONLY_DETAIL) and the
 * menus show their labels only.
 */
import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';
import { useApp } from '../components/AppContext';
import {
  FilterFacetContext,
  type FilterFacetCounts,
  type FilterFacetSource,
} from '../components/ui/FilterSelect.facets';
import { fetchLeadFacets, leadFacetsQuery } from '../lib/apiClients/leadFacets';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { queryKeys } from '../lib/queryKeys';
import type { LeadFacetDimension, LeadFacetsResponse } from '../types/leadFilters';
import { SEGMENT_OPTION_TO_CODE } from './lead-queue.filters';
import { leadQueueLenderRefs, leadsRequestFromSearchParams, leadsRequestUnresolved } from './lead-queue.request';
import './lead-queue.filterTools.css';

/** The FilterSelect labels that are counted, and the dimension each counts. */
export const FACET_LABEL_DIMENSIONS: Readonly<Record<string, LeadFacetDimension>> = {
  STATE: 'state',
  SEGMENT: 'segment',
  PRODUCT: 'product',
  APPROVAL: 'approval',
};

export const FACET_STALE_MS = 60_000;

/**
 * The count beside one option: the no-op option ('All …', 'Any approval')
 * shows the total; a known option its bucket (0 when absent); anything else
 * (a synthetic "2 states selected") none.
 */
export function facetCountForOption(
  dimension: LeadFacetDimension,
  data: LeadFacetsResponse,
  option: string,
): number | null {
  if (option.startsWith('All ') || option === 'Any approval') return data.total_matching;
  let bucket: string | null;
  if (dimension === 'state') bucket = /^[A-Z]{2}$/.test(option) ? option : null;
  else if (dimension === 'segment') bucket = SEGMENT_OPTION_TO_CODE[option] ?? null;
  else if (dimension === 'approval') bucket = option.toLowerCase();
  else bucket = option;
  if (bucket === null) return null;
  return data.buckets.find((candidate) => candidate.value === bucket)?.count ?? 0;
}

export function FilterFacetsProvider({ children }: { children: ReactNode }) {
  const [openLabel, setOpenLabel] = useState<string | null>(null);
  const [searchParams] = useSearchParams();
  const { actorEmail } = useApp();
  const configOptions = useConfigOptionsQuery();
  const dimension = openLabel === null ? null : FACET_LABEL_DIMENSIONS[openLabel] ?? null;
  // An unresolved "Assigned to me" reads nothing, exactly like the queue.
  const unresolved = leadsRequestUnresolved(searchParams, actorEmail);
  const request = leadsRequestFromSearchParams(
    searchParams,
    leadQueueLenderRefs(configOptions.data?.target_lender_refs),
    actorEmail,
  );
  const borrowerList = (request.geo?.borrowerIds?.length ?? 0) > 0;
  const facetQuery = dimension === null ? '' : leadFacetsQuery(dimension, request);
  const query = useQuery({
    // Keyed on the exact facet query (own filter already dropped), so a
    // change to the dimension's own filter reuses the cached counts.
    queryKey: queryKeys.leadFacets(dimension ?? '', [facetQuery]),
    queryFn: ({ signal }) => fetchLeadFacets(dimension ?? 'state', request, signal),
    enabled: dimension !== null && !unresolved && !borrowerList,
    staleTime: FACET_STALE_MS,
  });
  const source: FilterFacetSource = {
    setOpen: (label, open) => {
      setOpenLabel((current) => (open ? label : current === label ? null : current));
    },
    counts: (label): FilterFacetCounts | null => {
      const counted = FACET_LABEL_DIMENSIONS[label];
      if (!counted || label !== openLabel || unresolved || borrowerList) return null;
      const data = query.data;
      if (query.isError && !data) return { status: 'error', countFor: () => null };
      if (!data) return { status: 'loading', countFor: () => null };
      return { status: 'ready', countFor: (option) => facetCountForOption(counted, data, option) };
    },
  };
  return <FilterFacetContext value={source}>{children}</FilterFacetContext>;
}
