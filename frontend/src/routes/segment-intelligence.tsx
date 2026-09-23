import { useCallback, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { api, type LeadsPageResult, type SegmentFilterMode } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { SegmentCode, SegmentSummary } from '../types';
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
import { leadsQuery } from '../lib/leadsQuery';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';
import {
  activeSegmentsFromSearch,
  applySecondaryLeadFilters,
  CASHOUT_OPTIONS,
  chipFiltersAreDefault,
  chipFiltersFromSearch,
  CONSENT_OPTIONS,
  CONTACTABILITY_OPTIONS,
  formatSelectedSegmentLabel,
  INITIAL_ACTIVE_SEGMENTS,
  LIEN_OPTIONS,
  OCCUPANCY_OPTIONS,
  OWNER_LINK_OPTIONS,
  PURCHASE_OPTIONS,
  RECENCY_OPTIONS,
  searchParamsWithChipFilter,
  searchParamsWithoutSegmentFilters,
  secondaryPortfolioCriteria as buildSecondaryPortfolioCriteria,
  SEGMENT_MODE_OPTIONS,
  segmentCardQuerySelection,
  segmentFilterSearchKey,
  segmentModeFromSearch,
  segmentSearchParamsForState,
  VALID_SEGMENT_CODES,
  type ChipFilterKey,
} from './segment-intelligence.filters';

/**
 * Segment Intelligence — prototype composition: segment cards across the top
 * as a filter grid, ranked-borrower table below on the left, map / summary
 * preview on the right. This is the densest Module 0 screen and lines up 1:1
 * with the prototype's "segment-first" layout.
 */

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
  const locationCodes = useMemo(
    () => (footprint.ready && !footprint.usingFallback
      ? footprint.states.map((s) => s.state_code.toUpperCase())
      : []),
    [footprint.ready, footprint.states, footprint.usingFallback],
  );
  // flow-09: every filter below is DERIVED from the URL (no mirrored
  // useState), so refresh, Back/Forward and a shared link restore the view.
  // The memo key is the params this route's filters own, so a URL change
  // made by another feature keeps these objects (and their fetches) stable.
  const filterSearchKey = useMemo(() => segmentFilterSearchKey(searchParams), [searchParams]);
  const activeSegs = useMemo<SegmentCode[]>(() => {
    const parsed = activeSegmentsFromSearch(new URLSearchParams(filterSearchKey));
    return parsed.length > 0 ? parsed : INITIAL_ACTIVE_SEGMENTS;
  }, [filterSearchKey]);
  const segmentMode = useMemo<SegmentFilterMode>(
    () => segmentModeFromSearch(new URLSearchParams(filterSearchKey)),
    [filterSearchKey],
  );
  const chipFilters = useMemo(
    () => chipFiltersFromSearch(new URLSearchParams(filterSearchKey), { targetLenderOptions, locationCodes }),
    [filterSearchKey, targetLenderOptions, locationCodes],
  );
  const setChipFilter = useCallback(
    (key: ChipFilterKey, value: string) => {
      setSearchParams((current) => searchParamsWithChipFilter(current, key, value));
    },
    [setSearchParams],
  );
  const stateNameByCode = useMemo(
    () => new Map(Object.entries(locationToStates).map(([name, codes]) => [codes[0] ?? 'All', name])),
    [locationToStates],
  );
  // Geography drill state emitted by USChoroplethMap. State is the 2-char
  // USPS code; null = US level (no geography filter). ZIP is pushed down to
  // /api/leads so the ranked table follows the same state → ZIP cohort the
  // map counted. `county` is always null (the map has no county level) but
  // stays wired through for a future licensed county dataset.
  const [mapSelection, setMapSelection] = useState<MapSelection>({
    state: null,
    county: null,
    zip: null,
  });
  const handleMapSelection = useCallback((sel: MapSelection) => {
    setMapSelection(sel);
  }, []);
  const selectedLocationState = chipFilters.location === 'All' ? undefined : chipFilters.location;
  const secondaryPortfolioCriteria = useMemo(
    () => buildSecondaryPortfolioCriteria(chipFilters),
    [chipFilters],
  );
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
  // One request object feeds the fetcher AND the key (audit runtime-02).
  const leadsPageQuery = leadsQuery('segment-intelligence', {
    geo: serverGeo,
    opts: {
      segmentCodes: activeSegs.length > 0 ? activeSegs : undefined,
      segmentMode,
      portfolioCriteria: secondaryPortfolioCriteria,
    },
  });
  const {
    data: leadsData,
    warmingUp: leadsWarming,
    error: leadsError,
    manualRetry: retryLeads,
    isFetching: leadsFetching,
  } = useWarmingUpRetry<LeadsPageResult>(
    leadsPageQuery.fetcher,
    [leadsPageQuery.queryKey], // ignored while queryKey is passed
    { queryKey: leadsPageQuery.queryKey, keepPreviousData: true },
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

  // Primary segment and geography filters are pushed down to /api/leads
  // before LIMIT is applied; the secondary predicates run on the returned rows.
  const filtered = useMemo(() => applySecondaryLeadFilters(leads, chipFilters), [leads, chipFilters]);
  const uniqueCohortTotal = totalMatching ?? filtered.length;
  const rankedScopeEyebrow = hasSelectedSegments
    ? 'Ranked borrowers · selected segment cohort'
    : 'Ranked borrowers · full eligible queue';
  // The "cards are standalone memberships / don't add up" explanation lives once
  // at the segment cards (segmentCountScopeCopy); the ranked-table copy stays
  // scope-only to avoid repeating it on one screen.
  const rankedScopeCopy = hasSelectedSegments
    ? 'Showing the highest-ranked returned rows for the selected, de-duplicated segment cohort and geography drill-downs.'
    : 'No segment card is selected, so the table shows the full ranked lead queue after the secondary filters.';
  const segmentCountScopeCopy = hasSelectedSegments
    ? segmentMode === 'all'
      ? 'Card counts now show borrowers inside the All-selected intersection. Each selected card count should match the same unique borrower cohort.'
      : 'Card counts now show borrowers inside the Any-selected OR cohort. The cohort is de-duplicated before counting.'
    : 'Card counts are standalone segment memberships after filters. Borrowers can belong to more than one segment, so do not add the cards together.';

  const writeSegmentSearchParams = useCallback(
    (segments: SegmentCode[], mode: SegmentFilterMode) => {
      setSearchParams((current) => segmentSearchParamsForState(current, segments, mode));
    },
    [setSearchParams],
  );

  const toggleSeg = (code: SegmentCode) => {
    const next = activeSegs.includes(code)
      ? activeSegs.filter((s) => s !== code)
      : [...activeSegs, code];
    writeSegmentSearchParams(next, segmentMode);
  };

  const filtersDirty =
    activeSegs.length > 0 ||
    segmentMode !== 'any' ||
    !chipFiltersAreDefault(chipFilters) ||
    mapSelection.state !== null ||
    mapSelection.county !== null ||
    mapSelection.zip !== null;

  const clearAll = () => {
    setMapSelection({ state: null, county: null, zip: null });
    setSearchParams(searchParamsWithoutSegmentFilters(searchParams));
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
                onClick={() => writeSegmentSearchParams(activeSegs, option.mode)}
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
            value={stateNameByCode.get(chipFilters.location) ?? chipFilters.location}
            options={Object.keys(locationToStates)}
            onChange={(v) => setChipFilter('location', locationToStates[v]?.[0] ?? 'All')}
          />
          <FilterSelect
            label="OCCUPANCY"
            value={chipFilters.demographics}
            options={[...OCCUPANCY_OPTIONS]}
            onChange={(v) => setChipFilter('demographics', v)}
          />
          <FilterSelect
            label="RELATIONSHIP"
            value={chipFilters.lenderRelationship}
            options={[...LENDER_RELATIONSHIP_OPTIONS]}
            onChange={(v) => setChipFilter('lenderRelationship', v)}
          />
          <FilterSelect
            label="TARGET LIEN HOLDER"
            value={chipFilters.targetLenderRef}
            options={targetLenderOptions}
            onChange={(v) => setChipFilter('targetLenderRef', v)}
          />
          <FilterSelect
            label="LIEN"
            value={chipFilters.lien}
            options={[...LIEN_OPTIONS]}
            onChange={(v) => setChipFilter('lien', v)}
          />
          <FilterSelect
            label="OWNER LINK"
            value={chipFilters.ownerLink}
            options={[...OWNER_LINK_OPTIONS]}
            onChange={(v) => setChipFilter('ownerLink', v)}
          />
          <FilterSelect
            label="PURCHASE INTENT"
            value={chipFilters.purchase}
            options={[...PURCHASE_OPTIONS]}
            onChange={(v) => setChipFilter('purchase', v)}
          />
          <FilterSelect
            label="CASH-OUT"
            value={chipFilters.cashout}
            options={CASHOUT_OPTIONS}
            onChange={(v) => setChipFilter('cashout', v)}
          />
          <FilterSelect
            label="CONTACTABILITY"
            value={chipFilters.contactability}
            options={[...CONTACTABILITY_OPTIONS]}
            onChange={(v) => setChipFilter('contactability', v)}
          />
          <FilterSelect
            label="CONSENT"
            value={chipFilters.consent}
            options={[...CONSENT_OPTIONS]}
            onChange={(v) => setChipFilter('consent', v)}
          />
          <FilterSelect
            label="RECENCY"
            value={chipFilters.recency}
            options={[...RECENCY_OPTIONS]}
            onChange={(v) => setChipFilter('recency', v)}
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
