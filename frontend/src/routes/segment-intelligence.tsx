import { useCallback, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { LeadSummary, SegmentCode, SegmentSummary } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { SegmentCard } from '../components/mortgage/SegmentCard';
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

// PURCHASE INTENT: wired predicates that intentionally return zero rows
// until Cotality Building Permits + MLS Delta Shares are live. Copy on
// the filter calls this out to the presenter.
const PURCHASE_OPTIONS = ['All', 'Listed for sale', 'Recent permit activity', 'Both'] as const;
const CONTACTABILITY_OPTIONS = ['Eligible only', 'Any', 'Suppressed only'] as const;
const CONSENT_OPTIONS = ['Any', 'Opt-in', 'Opt-out', 'Unknown'] as const;
const RECENCY_OPTIONS = ['Any', 'Untouched 30d', 'Untouched 60d', 'Untouched 90d'] as const;

interface ChipFilters {
  location: string;
  demographics: string;
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
  lien: 'Any',
  ownerLink: 'All',
  purchase: 'All',
  cashout: 'Any',
  contactability: 'Eligible only',
  consent: 'Any',
  recency: 'Any',
};

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
  const footprint = useFootprint();
  const locationToStates = useMemo(
    () => buildLocationToStates(
      footprint.ready && !footprint.usingFallback ? footprint.states : [],
    ),
    [footprint.ready, footprint.states, footprint.usingFallback],
  );
  const [activeSegs, setActiveSegs] = useState<SegmentCode[]>(['itm']);
  const [chipFilters, setChipFilters] = useState<ChipFilters>(INITIAL_FILTERS);
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
    chipFilters.lien,
    chipFilters.ownerLink,
    chipFilters.purchase,
    chipFilters.recency,
  ]);
  const activeSegsKey = activeSegs.join(',');

  // Cold-start warming-up — segments + leads fetch independently so one
  // tile warming doesn't block the other (per-tile isolation, following
  // home.tsx). Each hook runs 6 retries / 5s apart = 30s total.
  const {
    data: segmentsData,
    warmingUp: segmentsWarming,
    error: segmentsError,
    manualRetry: retrySegments,
  } = useWarmingUpRetry<SegmentSummary[]>(
    (signal) =>
      api.segments(
        signal,
        activeSegs.length > 0 ? activeSegs : undefined,
        'all',
        secondaryPortfolioCriteria,
      ),
    [activeSegsKey, secondaryPortfolioCriteria],
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
  } = useWarmingUpRetry<LeadSummary[]>(
    (signal) =>
      api.leads(undefined, signal, serverGeo, {
        segmentCodes: activeSegs.length > 0 ? activeSegs : undefined,
        // Multi-select uses AND semantics: borrowers must match every
        // selected segment. The page copy mirrors this exact contract.
        segmentMode: 'all',
        portfolioCriteria: secondaryPortfolioCriteria,
      }),
    [activeSegsKey, secondaryPortfolioCriteria, serverGeo.state, serverGeo.county, serverGeo.zip],
  );
  const segments = useMemo(() => segmentsData ?? [], [segmentsData]);
  const segmentLabelByCode = useMemo(
    () => new Map<SegmentCode, string>(segments.map((s) => [s.code, s.name])),
    [segments],
  );
  const selectedSegmentLabel = useMemo(
    () => activeSegs.map((code) => segmentLabelByCode.get(code) ?? code).join(' + '),
    [activeSegs, segmentLabelByCode],
  );
  const leadsRefreshing = leadsData === null && !leadsWarming && !leadsError;
  const leads = useMemo(() => leadsData ?? [], [leadsData]);
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
    // PURCHASE INTENT -> listed_for_sale + has_permit. Both flags are
    // BLOCKED FALSE in gold until Cotality Building Permits + MLS Delta
    // Shares are live, so these predicates return 0 rows today. The
    // filter label carries a muted note explaining the data dependency.
    if (chipFilters.purchase === 'Listed for sale') {
      out = out.filter((l) => l.listed_for_sale === true);
    } else if (chipFilters.purchase === 'Recent permit activity') {
      out = out.filter((l) => l.has_permit === true);
    } else if (chipFilters.purchase === 'Both') {
      out = out.filter((l) => l.listed_for_sale === true && l.has_permit === true);
    }
    // Cash-out equity floor.
    const floor = EQUITY_FLOOR_USD[chipFilters.cashout] ?? 0;
    if (floor > 0) {
      out = out.filter((l) => l.equity_estimate >= floor);
    }
    return out;
  }, [leads, chipFilters]);

  const toggleSeg = (code: SegmentCode) => {
    setActiveSegs((cur) => (cur.includes(code) ? cur.filter((s) => s !== code) : [...cur, code]));
  };

  const filtersDirty =
    activeSegs.length > 0 ||
    JSON.stringify(chipFilters) !== JSON.stringify(INITIAL_FILTERS) ||
    mapSelection.state !== null ||
    mapSelection.county !== null ||
    mapSelection.zip !== null;

  const clearAll = () => {
    setActiveSegs([]);
    setChipFilters(INITIAL_FILTERS);
    setMapSelection({ state: null, county: null, zip: null });
  };

  const leadQueueHref = useMemo(() => {
    const params = new URLSearchParams();
    if (activeSegs.length === 1) {
      params.set('segment', activeSegs[0]);
    } else if (activeSegs.length > 1) {
      params.set('segment_codes', activeSegs.join(','));
      params.set('segment_mode', 'all');
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
        segments.length > 0
          ? `${segments.length} borrower ${segments.length === 1 ? 'segment' : 'segments'} · multi-select AND`
          : 'Borrower segments · multi-select uses AND'
      }
      lede="Segment cards are a multi-select AND filter: each added card narrows the ranked table to borrowers matching every selected segment. Secondary filters narrow by location, occupancy, lien, owner link, purchase intent, and equity. Counts refresh nightly."
      heroRight={
        filtersDirty ? (
          <Button size="sm" variant="ghost" icon="cross" onClick={clearAll}>
            Clear filters
          </Button>
        ) : undefined
      }
    >
      {segmentsWarming && (
        <WarmingUpBlock
          state={segmentsWarming}
          title="Segment catalog loading"
          compact
        />
      )}
      {leadsWarming && !segmentsWarming && (
        <WarmingUpBlock
          state={leadsWarming}
          title="Ranked borrowers loading"
          compact
        />
      )}
      {leadsRefreshing && !segmentsWarming && (
        <div className="status-callout">
          Refreshing ranked borrowers for the selected filters…
        </div>
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
      {segments.length === 0 && !loadErrorMsg && !segmentsWarming && (
        <div className="muted body mb-grid">
          Loading segments…
        </div>
      )}
      <div className="seg-grid">
        {segments.map((s) => (
          <SegmentCard
            key={s.code}
            segment={s}
            selected={activeSegs.includes(s.code)}
            onClick={() => toggleSeg(s.code)}
          />
        ))}
      </div>

      <div
        className="filter-row filter-row--spaced"
        aria-label="Secondary borrower filters"
      >
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
        <div
          className="filter-row__hint filter-row__hint--full muted"
        >
          Delta shares pending: listed-for-sale and permit predicates are
          blocked false until Cotality MLS and Building Permits Delta Shares are
          live; these options return no rows today.
        </div>
      </div>

      <div className="section-hdr">
        <div>
          <div className="eyebrow">Ranked borrowers · AND segment filter</div>
          <div className="h-2">
            {leadsRefreshing ? (
              'Refreshing ranked borrowers'
            ) : (
              <>
                {filtered.length >= 500 ? 'Top ' : ''}
                {filtered.length} ranked borrowers{' '}
                {activeSegs.length > 0 && (
                  <span className="muted fs-14">
                    · segment filter: {selectedSegmentLabel}
                    {activeSegs.length > 1 ? ' · must match every selected segment' : ''}
                  </span>
                )}
              </>
            )}
          </div>
          {!leadsRefreshing && filtered.length >= 500 && (
            <div className="muted fs-14">
              Showing the highest-ranked rows; segment cards and map show
              marketable population.
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

      <div className="layoutA-grid">
        <LeadTable leads={filtered} />
        <USChoroplethMap
          height={520}
          segmentFilter={activeSegs}
          segmentFilterMode="all"
          portfolioCriteria={secondaryPortfolioCriteria}
          onSelectionChange={handleMapSelection}
        />
      </div>
    </PageShell>
  );
}
