import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, type LeadsPageResult, type SegmentFilterMode } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { LeadSummary, SegmentCode, SegmentSummary } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { SegmentCard, SegmentCardSkeleton } from '../components/mortgage/SegmentCard';
import { LeadTable } from '../components/mortgage/LeadTable';
import {
  USChoroplethMap,
  type MapSelection,
} from '../components/mortgage/USChoroplethMap';
import { Button, Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { FilterSelect } from '../components/ui/FilterSelect';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { useFootprint } from '../components/FootprintProvider';
import { queryKeys } from '../lib/queryKeys';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { isPublicLenderRef, LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';

/**
 * Segment Intelligence — prototype composition: segment cards across the top
 * as a filter grid, ranked-borrower table below on the left, map / summary
 * preview on the right. This is the densest Module 0 screen and lines up 1:1
 * with the prototype's "segment-first" layout.
 */

// Equity thresholds expressed as a minimum equity-to-AVM ratio. We don't
// have AVM on LeadSummary (it's on Borrower360), so the predicate uses
// equity_estimate as a lower bound proxy. Good enough for the UI filter.
const EQUITY_FLOOR_USD: Record<string, number> = {
  Any: 0,
  'Equity ≥ 15%': 50_000,
  'Equity ≥ 25%': 150_000,
  'Equity ≥ 40%': 300_000,
};

// OCCUPANCY replaced "Homeowner / First-time buyer / Age 55+" (no
// predicate available) with a real occupancy predicate against
// `is_owner_occupied`. Options re-phrased to match the signal we actually
// carry from gold.borrower_360.
const OCCUPANCY_OPTIONS = ['All', 'Owner-occupied', 'Non-owner-occupied'] as const;
// LIEN (secondary filter) operates on the open-lien state of the subject
// property. Distinct from the portfolio-builder's primary lien-status
// filter (which discriminates at the population level). Maps to
// `current_lien_balance` + `second_pos_amount` from gold.borrower_360.
const LIEN_OPTIONS = ['Any', 'Open 1st lien only', 'Open 2nd lien / HELOC', 'Free & clear'] as const;

// OWNER LINK buckets use `related_property_count` from the Owner Link
// bridge. Bucket thresholds match how LOs typically think about borrower
// portfolios (single / small / large).
const OWNER_LINK_OPTIONS = ['All', 'Single-property owner', 'Multi-property (2-4)', 'Portfolio investor (5+)'] as const;

// PURCHASE / EQUITY-CREDIT INTENT: MLS listing is a live purchase trigger.
// HELOC intent is a live Cotality propensity model signal; true filed
// building-permit activity remains a separate pending source.
const PURCHASE_OPTIONS = ['All', 'Listed for sale', 'HELOC intent', 'Both'] as const;
const CONTACTABILITY_OPTIONS = ['Eligible only', 'Any', 'Suppressed only'] as const;
const CONSENT_OPTIONS = ['Any', 'Opt-in', 'Opt-out', 'Unknown'] as const;
const RECENCY_OPTIONS = ['Any', 'Untouched 30d', 'Untouched 60d', 'Untouched 90d'] as const;
const SEGMENT_MODE_OPTIONS: Array<{
  mode: SegmentFilterMode;
  label: string;
  description: string;
}> = [
  {
    mode: 'any',
    label: 'Any selected',
    description: 'OR · de-duped',
  },
  {
    mode: 'all',
    label: 'All selected',
    description: 'AND · intersection',
  },
];
const VALID_SEGMENT_CODES: readonly SegmentCode[] = ['itm', 'listed', 'permit', 'investor', 'equity', 'retention'];
const VALID_SEGMENT_CODE_SET = new Set<string>(VALID_SEGMENT_CODES);

interface ChipFilters {
  location: string;
  demographics: string;
  lenderRelationship: string;
  targetLenderRef: string;
  lien: string;
  ownerLink: string;
  purchase: string;
  cashout: string;
  contactability: string;
  consent: string;
  recency: string;
}

const INITIAL_FILTERS: ChipFilters = {
  location: 'All',
  demographics: 'All',
  lenderRelationship: 'All',
  targetLenderRef: 'All',
  lien: 'Any',
  ownerLink: 'All',
  purchase: 'All',
  cashout: 'Any',
  contactability: 'Eligible only',
  consent: 'Any',
  recency: 'Any',
};

export const INITIAL_ACTIVE_SEGMENTS: SegmentCode[] = [];

export function activeSegmentsFromSearch(searchParams: URLSearchParams): SegmentCode[] {
  const raw = [
    searchParams.get('segment'),
    searchParams.get('segments'),
    searchParams.get('segment_codes'),
  ]
    .filter(Boolean)
    .join(',');
  const selected: SegmentCode[] = [];
  for (const value of raw.split(',')) {
    const code = value.trim().toLowerCase();
    if (!VALID_SEGMENT_CODE_SET.has(code) || selected.includes(code as SegmentCode)) continue;
    selected.push(code as SegmentCode);
  }
  return selected;
}

export function segmentModeFromSearch(searchParams: URLSearchParams): SegmentFilterMode {
  return searchParams.get('segment_mode')?.toLowerCase() === 'all' ? 'all' : 'any';
}

export function segmentSearchParamsForState(
  searchParams: URLSearchParams,
  segments: SegmentCode[],
  mode: SegmentFilterMode,
): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  next.delete('segment');
  next.delete('segments');
  next.delete('segment_codes');
  if (segments.length === 1) {
    next.set('segment', segments[0]);
  } else if (segments.length > 1) {
    next.set('segment_codes', segments.join(','));
  }
  if (segments.length > 1) {
    next.set('segment_mode', mode);
  } else {
    next.delete('segment_mode');
  }
  return next;
}

export function formatSelectedSegmentLabel(
  labels: string[],
  segmentMode: SegmentFilterMode,
): string {
  if (labels.length <= 1) return labels.join('');
  const conjunction = segmentMode === 'all' ? 'and' : 'or';
  if (labels.length === 2) return `${labels[0]} ${conjunction} ${labels[1]}`;
  return `${labels.slice(0, -1).join(', ')}, ${conjunction} ${labels[labels.length - 1]}`;
}

export function segmentCardQuerySelection(
  segments: SegmentCode[],
  mode: SegmentFilterMode,
): { segmentCodes?: SegmentCode[]; segmentMode: SegmentFilterMode } {
  return segments.length > 0
    ? { segmentCodes: segments, segmentMode: mode }
    : { segmentCodes: undefined, segmentMode: 'any' };
}

export function lenderFiltersFromSearch(
  searchParams: URLSearchParams,
  targetLenderOptions: readonly string[] = [],
): Pick<ChipFilters, 'lenderRelationship' | 'targetLenderRef' | 'ownerLink' | 'purchase'> {
  const relationship = searchParams.get('lender_relationship');
  const lenderRelationship = LENDER_RELATIONSHIP_OPTIONS.includes(
    relationship as (typeof LENDER_RELATIONSHIP_OPTIONS)[number],
  )
    ? relationship!
    : 'All';
  const target = searchParams.get('target_lender_ref');
  const targetLenderRef = isPublicLenderRef(target, targetLenderOptions) ? target! : 'All';
  const ownerLink = searchParams.get('owner_link');
  const purchase = searchParams.get('purchase_intent');
  return {
    lenderRelationship,
    targetLenderRef,
    ownerLink: OWNER_LINK_OPTIONS.includes(ownerLink as (typeof OWNER_LINK_OPTIONS)[number])
      ? ownerLink!
      : 'All',
    purchase: PURCHASE_OPTIONS.includes(purchase as (typeof PURCHASE_OPTIONS)[number])
      ? purchase!
      : 'All',
  };
}

/**
 * Build the LOCATION-dropdown map for the current tenant footprint.
 *
 * Keeps the "All" entry first (no state filter), then one entry per
 * footprint state. The display label is the backend-provided state name,
 * so the UI adjusts naturally when live coverage changes. While footprint
 * metadata is still loading or on fallback, consumers pass an empty list so
 * only "All" appears instead of exposing generic US-state metadata as if it
 * were tenant coverage.
 */
function buildLocationToStates(
  states: ReadonlyArray<{ state_code: string; state_name: string }>,
): Record<string, string[]> {
  const out: Record<string, string[]> = { All: [] };
  for (const s of states) {
    const code = s.state_code.toUpperCase();
    out[s.state_name] = [code];
  }
  return out;
}

export default function SegmentIntelligence() {
  const [searchParams, setSearchParams] = useSearchParams();
  const footprint = useFootprint();
  const configOptionsQuery = useConfigOptionsQuery();
  const targetLenderOptions = useMemo(() => {
    const values = configOptionsQuery.data?.target_lender_refs?.filter(Boolean);
    return values && values.length > 0 ? values : ['All'];
  }, [configOptionsQuery.data?.target_lender_refs]);
  const locationToStates = useMemo(
    () => buildLocationToStates(
      footprint.ready && !footprint.usingFallback ? footprint.states : [],
    ),
    [footprint.ready, footprint.states, footprint.usingFallback],
  );
  const [activeSegs, setActiveSegs] = useState<SegmentCode[]>(() => activeSegmentsFromSearch(searchParams));
  const [segmentMode, setSegmentMode] = useState<SegmentFilterMode>(() => segmentModeFromSearch(searchParams));
  const [chipFilters, setChipFilters] = useState<ChipFilters>(() => ({
    ...INITIAL_FILTERS,
    ...lenderFiltersFromSearch(searchParams, targetLenderOptions),
  }));
  useEffect(() => {
    const next = lenderFiltersFromSearch(searchParams, targetLenderOptions);
    setChipFilters((current) => (
      current.lenderRelationship === next.lenderRelationship
      && current.targetLenderRef === next.targetLenderRef
      && current.ownerLink === next.ownerLink
      && current.purchase === next.purchase
        ? current
        : { ...current, ...next }
    ));
  }, [searchParams, targetLenderOptions]);
  useEffect(() => {
    const nextSegments = activeSegmentsFromSearch(searchParams);
    setActiveSegs((current) => (
      current.join(',') === nextSegments.join(',') ? current : nextSegments
    ));
    const nextMode = segmentModeFromSearch(searchParams);
    setSegmentMode((current) => (current === nextMode ? current : nextMode));
  }, [searchParams]);
  // Geography drill state emitted by USChoroplethMap. State is the 2-char
  // USPS code; null = US level (no geography filter). County/ZIP are pushed
  // down to /api/leads so the ranked table follows the same state → county
  // → ZIP cohort the map counted.
  const [mapSelection, setMapSelection] = useState<MapSelection>({
    state: null,
    county: null,
    zip: null,
  });
  const handleMapSelection = useCallback((sel: MapSelection) => {
    setMapSelection(sel);
  }, []);
  const selectedLocationState = (locationToStates[chipFilters.location] ?? [])[0];
  const secondaryPortfolioCriteria = useMemo(() => {
    const criteria: Record<string, string> = {};
    if (chipFilters.demographics !== 'All') {
      criteria.occupancy = chipFilters.demographics;
    }
    if (chipFilters.lenderRelationship !== 'All') {
      criteria.lender_relationship = chipFilters.lenderRelationship;
    }
    if (chipFilters.targetLenderRef !== 'All') {
      criteria.target_lender_ref = chipFilters.targetLenderRef;
    }
    if (chipFilters.lien === 'Open 1st lien only') {
      criteria.lien_status = 'Open 1st lien';
    } else if (chipFilters.lien === 'Open 2nd lien / HELOC') {
      criteria.lien_status = 'Open HELOC';
    } else if (chipFilters.lien === 'Free & clear') {
      criteria.lien_status = 'Free & clear';
    }
    if (chipFilters.ownerLink !== 'All') {
      criteria.owner_link = chipFilters.ownerLink;
    }
    if (chipFilters.purchase !== 'All') {
      criteria.purchase_intent = chipFilters.purchase;
    }
    if (chipFilters.cashout === 'Equity ≥ 15%') {
      criteria.min_equity_pct_label = '≥ 15%';
    } else if (chipFilters.cashout === 'Equity ≥ 25%') {
      criteria.min_equity_pct_label = '≥ 25%';
    } else if (chipFilters.cashout === 'Equity ≥ 40%') {
      criteria.min_equity_pct_label = '≥ 40%';
    }
    if (chipFilters.contactability !== 'Any') {
      criteria.marketing_eligibility = chipFilters.contactability;
    }
    if (chipFilters.consent !== 'Any') {
      criteria.consent_status = chipFilters.consent;
    }
    if (chipFilters.recency !== 'Any') {
      criteria.recency = chipFilters.recency;
    }
    return criteria;
  }, [
    chipFilters.cashout,
    chipFilters.contactability,
    chipFilters.consent,
    chipFilters.demographics,
    chipFilters.lenderRelationship,
    chipFilters.lien,
    chipFilters.ownerLink,
    chipFilters.purchase,
    chipFilters.recency,
    chipFilters.targetLenderRef,
  ]);
  const activeSegsKey = activeSegs.join(',');
  const hasSelectedSegments = activeSegs.length > 0;

  // Cold-start warming-up — segment cards + leads fetch independently so one
  // tile warming doesn't block the other (per-tile isolation, following
  // home.tsx). Each hook runs 6 retries / 5s apart = 30s total.
  const {
    segmentCodes: segmentCardCodes,
    segmentMode: segmentCardMode,
  } = segmentCardQuerySelection(activeSegs, segmentMode);

  const {
    data: segmentsData,
    warmingUp: segmentsWarming,
    error: segmentsError,
    manualRetry: retrySegments,
    isFetching: segmentsFetching,
  } = useWarmingUpRetry<SegmentSummary[]>(
    (signal) =>
      api.segments(
        signal,
        segmentCardCodes,
        segmentCardMode,
        secondaryPortfolioCriteria,
      ),
    [activeSegsKey, segmentCardCodes, segmentCardMode, secondaryPortfolioCriteria],
    {
      queryKey: queryKeys.segments([
        hasSelectedSegments ? 'selected-cohort' : 'standalone',
        activeSegsKey,
        segmentCardMode,
        JSON.stringify(secondaryPortfolioCriteria),
      ]),
      keepPreviousData: true,
    },
  );
  const serverGeo = useMemo(
    () => ({
      state: mapSelection.state ?? selectedLocationState,
      county: mapSelection.county ?? undefined,
      zip: mapSelection.zip ?? undefined,
    }),
    [mapSelection.state, mapSelection.county, mapSelection.zip, selectedLocationState],
  );
  const {
    data: leadsData,
    warmingUp: leadsWarming,
    error: leadsError,
    manualRetry: retryLeads,
    isFetching: leadsFetching,
  } = useWarmingUpRetry<LeadsPageResult>(
    (signal) =>
      api.leadsPage(undefined, signal, serverGeo, {
        segmentCodes: activeSegs.length > 0 ? activeSegs : undefined,
        segmentMode,
        portfolioCriteria: secondaryPortfolioCriteria,
      }),
    [activeSegsKey, segmentMode, secondaryPortfolioCriteria, serverGeo.state, serverGeo.county, serverGeo.zip],
    {
      queryKey: queryKeys.leads([
        'segment-intelligence',
        activeSegsKey,
        segmentMode,
        JSON.stringify(secondaryPortfolioCriteria),
        serverGeo.state ?? '',
        serverGeo.county ?? '',
        serverGeo.zip ?? '',
      ]),
      keepPreviousData: true,
    },
  );
  const segments = useMemo(() => segmentsData ?? [], [segmentsData]);
  const segmentLabelByCode = useMemo(
    () => new Map<SegmentCode, string>(segments.map((s) => [s.code, s.name])),
    [segments],
  );
  const selectedSegmentLabel = useMemo(
    () => {
      const labels = activeSegs.map((code) => segmentLabelByCode.get(code) ?? code);
      return formatSelectedSegmentLabel(labels, segmentMode);
    },
    [activeSegs, segmentLabelByCode, segmentMode],
  );
  const segmentsInitialLoading = segmentsData === null && !segmentsWarming && !segmentsError;
  const leadsInitialLoading = leadsData === null && !leadsWarming && !leadsError;
  const segmentsRefetchWarming = segmentsData !== null && segmentsWarming !== null;
  const leadsRefetchWarming = leadsData !== null && leadsWarming !== null;
  const segmentsUpdating = segmentsData !== null && (segmentsFetching || segmentsRefetchWarming);
  const leadsUpdating = leadsData !== null && (leadsFetching || leadsRefetchWarming);
  const pageUpdating = segmentsUpdating || leadsUpdating;
  const pageStatusLabel = segmentsRefetchWarming
    ? `${segmentsWarming.label} (${segmentsWarming.attempt}/${segmentsWarming.maxAttempts})`
    : leadsRefetchWarming
      ? `${leadsWarming.label} (${leadsWarming.attempt}/${leadsWarming.maxAttempts})`
      : 'updating';
  const leadsStatusLabel = leadsRefetchWarming
    ? `${leadsWarming.label} (${leadsWarming.attempt}/${leadsWarming.maxAttempts})`
    : 'updating';
  const leads = useMemo(() => leadsData?.leads ?? [], [leadsData]);
  const totalMatching = leadsData?.totalMatching ?? null;
  const truncatedAt = leadsData?.truncatedAt ?? null;
  const retryAll = useCallback(() => {
    retrySegments();
    retryLeads();
  }, [retrySegments, retryLeads]);
  const loadErrorMsg =
    segmentsError
      ? `Couldn't load segments: ${segmentsError.message}`
      : leadsError
        ? `Couldn't load leads: ${leadsError.message}`
        : null;

  const filtered = useMemo(() => {
    let out = leads;
    // Primary segment and geography filters are pushed down to /api/leads
    // before LIMIT is applied. The predicates below are secondary fields
    // carried on the ranked lead rows and still run locally.
    // OCCUPANCY -> is_owner_occupied predicate.
    if (chipFilters.demographics === 'Owner-occupied') {
      out = out.filter((l) => l.is_owner_occupied === true);
    } else if (chipFilters.demographics === 'Non-owner-occupied') {
      out = out.filter((l) => l.is_owner_occupied === false);
    }
    // LIEN (secondary) -> current_lien_balance + second_pos_amount.
    if (chipFilters.lien === 'Open 1st lien only') {
      out = out.filter(
        (l) =>
          (l.current_lien_balance ?? 0) > 0 &&
          (l.second_pos_amount == null || l.second_pos_amount === 0),
      );
    } else if (chipFilters.lien === 'Open 2nd lien / HELOC') {
      out = out.filter((l) => (l.second_pos_amount ?? 0) > 0);
    } else if (chipFilters.lien === 'Free & clear') {
      out = out.filter((l) => (l.current_lien_balance ?? 0) === 0);
    }
    // OWNER LINK -> related_property_count buckets.
    if (chipFilters.ownerLink === 'Single-property owner') {
      out = out.filter((l) => (l.related_property_count ?? 1) <= 1);
    } else if (chipFilters.ownerLink === 'Multi-property (2-4)') {
      const c = (l: LeadSummary) => l.related_property_count ?? 1;
      out = out.filter((l) => c(l) >= 2 && c(l) <= 4);
    } else if (chipFilters.ownerLink === 'Portfolio investor (5+)') {
      out = out.filter((l) => (l.related_property_count ?? 1) >= 5);
    }
    // PURCHASE INTENT -> listed_for_sale + Cotality HELOC propensity. True
    // filed building permits remain a separate pending source and must not
    // be inferred from the propensity model.
    const hasHelocIntent = (l: LeadSummary) => l.has_heloc_propensity_trigger === true;
    if (chipFilters.purchase === 'Listed for sale') {
      out = out.filter((l) => l.listed_for_sale === true);
    } else if (chipFilters.purchase === 'HELOC intent') {
      out = out.filter((l) => hasHelocIntent(l));
    } else if (chipFilters.purchase === 'Both') {
      out = out.filter((l) => l.listed_for_sale === true && hasHelocIntent(l));
    }
    // Cash-out equity floor.
    const floor = EQUITY_FLOOR_USD[chipFilters.cashout] ?? 0;
    if (floor > 0) {
      out = out.filter((l) => l.equity_estimate >= floor);
    }
    return out;
  }, [leads, chipFilters]);
  const uniqueCohortTotal = totalMatching ?? filtered.length;
  const rankedScopeEyebrow = hasSelectedSegments
    ? 'Ranked borrowers · selected segment cohort'
    : 'Ranked borrowers · full eligible queue';
  const rankedScopeCopy = hasSelectedSegments
    ? 'Showing the highest-ranked returned rows; segment cards remain standalone counts, while the table and map show the selected, de-duplicated segment cohort and geography drill-downs.'
    : 'No segment card is selected, so the table shows the full ranked lead queue after the secondary filters. Segment cards are standalone memberships and are not meant to add up to this queue total.';
  const segmentCountScopeCopy = hasSelectedSegments
    ? segmentMode === 'all'
      ? 'Card counts now show borrowers inside the All-selected intersection. Each selected card count should match the same unique borrower cohort.'
      : 'Card counts now show borrowers inside the Any-selected OR cohort. The cohort is de-duplicated before counting.'
    : 'Card counts are standalone segment memberships after filters. Borrowers can belong to more than one segment, so do not add the cards together.';

  const writeSegmentSearchParams = useCallback(
    (segments: SegmentCode[], mode: SegmentFilterMode) => {
      setSearchParams(segmentSearchParamsForState(searchParams, segments, mode));
    },
    [searchParams, setSearchParams],
  );

  const toggleSeg = (code: SegmentCode) => {
    const next = activeSegs.includes(code)
      ? activeSegs.filter((s) => s !== code)
      : [...activeSegs, code];
    setActiveSegs(next);
    writeSegmentSearchParams(next, segmentMode);
  };

  const filtersDirty =
    activeSegs.length > 0 ||
    segmentMode !== 'any' ||
    JSON.stringify(chipFilters) !== JSON.stringify(INITIAL_FILTERS) ||
    mapSelection.state !== null ||
    mapSelection.county !== null ||
    mapSelection.zip !== null;

  const clearAll = () => {
    setActiveSegs([]);
    setSegmentMode('any');
    setChipFilters(INITIAL_FILTERS);
    setMapSelection({ state: null, county: null, zip: null });
    const next = new URLSearchParams(searchParams);
    next.delete('lender_relationship');
    next.delete('target_lender_ref');
    next.delete('owner_link');
    next.delete('purchase_intent');
    next.delete('segment');
    next.delete('segments');
    next.delete('segment_codes');
    next.delete('segment_mode');
    setSearchParams(next);
  };

  const leadQueueHref = useMemo(() => {
    const params = new URLSearchParams();
    if (activeSegs.length === 1) {
      params.set('segment', activeSegs[0]);
    } else if (activeSegs.length > 1) {
      params.set('segment_codes', activeSegs.join(','));
      params.set('segment_mode', segmentMode);
    }
    if (mapSelection.zip) {
      params.set('zip', mapSelection.zip);
    } else if (mapSelection.county) {
      params.set('county', mapSelection.county);
    } else if (mapSelection.state ?? selectedLocationState) {
      params.set('state', mapSelection.state ?? selectedLocationState ?? '');
    }
    Object.entries(secondaryPortfolioCriteria).forEach(([key, value]) => {
      params.set(key, value);
    });
    const query = params.toString();
    return query ? `/lead-queue?${query}` : '/lead-queue';
  }, [
    activeSegs,
    segmentMode,
    mapSelection.zip,
    mapSelection.county,
    mapSelection.state,
    selectedLocationState,
    secondaryPortfolioCriteria,
  ]);

  return (
    <PageShell
      eyebrow="Segments"
      title={
        (segments.length || VALID_SEGMENT_CODES.length) > 0
          ? `${segments.length || VALID_SEGMENT_CODES.length} borrower ${(segments.length || VALID_SEGMENT_CODES.length) === 1 ? 'segment' : 'segments'} · ${
              hasSelectedSegments ? 'selected cohort counts' : 'standalone counts'
            }`
          : `Borrower segments · ${hasSelectedSegments ? 'selected cohort counts' : 'standalone counts'}`
      }
      lede={hasSelectedSegments
        ? 'Card counts, the map, and the ranked table are using the same selected segment mode. Any selected is a de-duplicated OR cohort; All selected is the borrower intersection.'
        : 'Cards show standalone segment membership counts after filters. Select cards, then choose Any selected for a de-duplicated OR cohort or All selected for borrowers in every selected segment.'}
      heroRight={
        <Button
          size="sm"
          variant="ghost"
          icon="cross"
          disabled={!filtersDirty}
          aria-disabled={!filtersDirty}
          onClick={clearAll}
        >
          Clear filters
        </Button>
      }
    >
      {segmentsWarming && segmentsData === null && (
        <WarmingUpBlock
          state={segmentsWarming}
          title="Segment catalog loading"
          compact
        />
      )}
      {leadsWarming && leadsData === null && !segmentsWarming && (
        <WarmingUpBlock
          state={leadsWarming}
          title="Ranked borrowers loading"
          compact
        />
      )}
      {loadErrorMsg && !segmentsWarming && !leadsWarming && (
        <div
          role="alert"
          className="status-callout status-callout--danger"
        >
          <span>{loadErrorMsg}</span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={retryAll}
            aria-label="Retry loading segments and leads"
          >
            Retry
          </button>
        </div>
      )}
      {segments.length === 0 && !loadErrorMsg && !segmentsWarming && !segmentsInitialLoading && (
        <div className="muted body mb-grid">
          Loading segments…
        </div>
      )}
      <div
        className={`seg-grid stable-refresh-region ${segmentsUpdating ? 'is-updating' : ''}`}
        aria-busy={segmentsUpdating || segmentsInitialLoading}
      >
        {segments.length > 0
          ? segments.map((s) => (
              <SegmentCard
                key={s.code}
                segment={s}
                selected={activeSegs.includes(s.code)}
                updating={segmentsUpdating}
                onClick={() => toggleSeg(s.code)}
              />
            ))
          : Array.from({ length: VALID_SEGMENT_CODES.length }).map((_, index) => (
              <SegmentCardSkeleton key={index} />
            ))}
      </div>

      <div
        className="filter-row filter-row--spaced filter-row--stacked"
        aria-label="Secondary borrower filters"
      >
        <div className="segment-mode-control" aria-label="Selected segment match mode">
          <div>
            <div className="eyebrow">Selected segments</div>
            <div className="muted fs-12">
              Any selected is a de-duplicated OR cohort. All selected is the AND intersection where each borrower must appear in every selected segment.
            </div>
            {totalMatching !== null && (
              <div className="segment-mode-control__total">
                <span className="eyebrow">{hasSelectedSegments ? 'Selected cohort' : 'Ranked queue'}</span>
                <span className="num">{uniqueCohortTotal.toLocaleString()}</span>
                <span className="muted fs-12">unique ranked borrowers</span>
                <span
                  className={`chip chip--neutral chip--compact stable-status-chip ${pageUpdating ? '' : 'is-idle'}`}
                  aria-hidden={!pageUpdating}
                >
                  {pageStatusLabel}
                </span>
              </div>
            )}
          </div>
          <div className="segmented segmented--inline" role="group" aria-label="Segment match mode">
            {SEGMENT_MODE_OPTIONS.map((option) => (
              <button
                key={option.mode}
                type="button"
                className={segmentMode === option.mode ? 'is-active' : ''}
                aria-pressed={segmentMode === option.mode}
                onClick={() => {
                  setSegmentMode(option.mode);
                  writeSegmentSearchParams(activeSegs, option.mode);
                }}
              >
                <span>{option.label}</span>
                <span className="segmented__meta">{option.description}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="filter-row__controls">
          <FilterSelect
            label="LOCATION"
            value={chipFilters.location}
            options={Object.keys(locationToStates)}
            onChange={(v) => setChipFilters((f) => ({ ...f, location: v }))}
          />
          <FilterSelect
            label="OCCUPANCY"
            value={chipFilters.demographics}
            options={[...OCCUPANCY_OPTIONS]}
            onChange={(v) => setChipFilters((f) => ({ ...f, demographics: v }))}
          />
          <FilterSelect
            label="RELATIONSHIP"
            value={chipFilters.lenderRelationship}
            options={[...LENDER_RELATIONSHIP_OPTIONS]}
            onChange={(v) => setChipFilters((f) => ({ ...f, lenderRelationship: v }))}
          />
          <FilterSelect
            label="TARGET LIEN HOLDER"
            value={chipFilters.targetLenderRef}
            options={targetLenderOptions}
            onChange={(v) => setChipFilters((f) => ({ ...f, targetLenderRef: v }))}
          />
          <FilterSelect
            label="LIEN"
            value={chipFilters.lien}
            options={[...LIEN_OPTIONS]}
            onChange={(v) => setChipFilters((f) => ({ ...f, lien: v }))}
          />
          <FilterSelect
            label="OWNER LINK"
            value={chipFilters.ownerLink}
            options={[...OWNER_LINK_OPTIONS]}
            onChange={(v) => setChipFilters((f) => ({ ...f, ownerLink: v }))}
          />
          <FilterSelect
            label="PURCHASE INTENT"
            value={chipFilters.purchase}
            options={[...PURCHASE_OPTIONS]}
            onChange={(v) => setChipFilters((f) => ({ ...f, purchase: v }))}
          />
          <FilterSelect
            label="CASH-OUT"
            value={chipFilters.cashout}
            options={Object.keys(EQUITY_FLOOR_USD)}
            onChange={(v) => setChipFilters((f) => ({ ...f, cashout: v }))}
          />
          <FilterSelect
            label="CONTACTABILITY"
            value={chipFilters.contactability}
            options={[...CONTACTABILITY_OPTIONS]}
            onChange={(v) => setChipFilters((f) => ({ ...f, contactability: v }))}
          />
          <FilterSelect
            label="CONSENT"
            value={chipFilters.consent}
            options={[...CONSENT_OPTIONS]}
            onChange={(v) => setChipFilters((f) => ({ ...f, consent: v }))}
          />
          <FilterSelect
            label="RECENCY"
            value={chipFilters.recency}
            options={[...RECENCY_OPTIONS]}
            onChange={(v) => setChipFilters((f) => ({ ...f, recency: v }))}
          />
        </div>
        <div
          className="filter-row__hint filter-row__hint--full muted"
        >
          {segmentCountScopeCopy}{' '}
          Selected cards {segmentMode === 'all' ? 'must all match the same borrower' : 'stack into one de-duplicated OR cohort'} for the table, map, and lead-queue drilldown.
          Listed-for-sale is
          backed by live Cotality MLS rows. HELOC intent is
          backed by Cotality HELOC propensity; filed building-permit records
          remain a separate pending source.
        </div>
      </div>

      <div className="section-hdr">
        <div>
          <div className="eyebrow">{rankedScopeEyebrow}</div>
          <div className="h-2 section-hdr__titleline">
            {leadsInitialLoading ? (
              <>Ranked borrowers loading</>
            ) : (
              <>
                {truncatedAt ? 'Top ' : ''}
                {filtered.length} ranked borrowers
                {totalMatching !== null && totalMatching !== filtered.length && (
                  <> of {totalMatching.toLocaleString()} total matching filters</>
                )}
              </>
            )}{' '}
            {activeSegs.length > 0 && (
              <span className="muted fs-14">
                · segment filter: {selectedSegmentLabel}
                {activeSegs.length > 1
                  ? segmentMode === 'all'
                    ? ' · all selected segments'
                    : ' · any selected segment, de-duplicated'
                  : ''}
              </span>
            )}
            <span
              className={`chip chip--neutral chip--compact stable-status-chip ${leadsUpdating ? '' : 'is-idle'}`}
              aria-hidden={!leadsUpdating}
            >
              {leadsStatusLabel}
            </span>
          </div>
          {leadsInitialLoading && (
            <div className="muted fs-14">
              Fetching the ranked borrower cohort with the selected filters.
            </div>
          )}
          {truncatedAt && (
            <div className="muted fs-14">
              {rankedScopeCopy}
            </div>
          )}
          {(mapSelection.state || mapSelection.county || mapSelection.zip) && (
            <div className="chip-row mt-2">
              {mapSelection.state && (
                <Chip variant="neutral" icon="pin">
                  state: {mapSelection.state}
                </Chip>
              )}
              {mapSelection.county && (
                <Chip variant="neutral" icon="pin">
                  county: {mapSelection.county}
                </Chip>
              )}
              {mapSelection.zip && (
                <Chip variant="neutral" icon="pin">
                  zip: {mapSelection.zip}
                </Chip>
              )}
              <Button
                size="sm"
                variant="ghost"
                icon="cross"
                onClick={() => setMapSelection({ state: null, county: null, zip: null })}
              >
                Clear geography
              </Button>
            </div>
          )}
        </div>
        <Link to={leadQueueHref} className="btn">
          Deep-dive lead queue
          <Icon name="chevright" size={14} />
        </Link>
      </div>

      <div
        className={`layoutA-grid layoutA-grid--segment-workbench stable-refresh-region ${leadsUpdating ? 'is-updating' : ''}`}
        aria-busy={leadsUpdating || leadsInitialLoading}
      >
        <LeadTable
          leads={filtered}
          totalMatching={totalMatching}
          truncatedAt={truncatedAt}
        />
        <USChoroplethMap
          height={520}
          segmentFilter={activeSegs}
          segmentFilterMode={segmentMode}
          portfolioCriteria={secondaryPortfolioCriteria}
          onSelectionChange={handleMapSelection}
        />
      </div>
    </PageShell>
  );
}
