import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, isAbortError } from '../lib/api';
import type { LeadSummary, SegmentSummary } from '../types';
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
import { useFootprint } from '../components/FootprintProvider';

/**
 * Segment Intelligence — prototype composition: segment cards across the top
 * as a filter grid, ranked-borrower table below on the left, map / summary
 * preview on the right. This is the densest Module 0 screen and lines up 1:1
 * with the prototype's "segment-first" layout.
 */

// Display-label -> US state code map (matches LeadSummary.state). The set
// of entries is derived from the tenant's footprint via FootprintProvider
// at render time (see `buildLocationToStates` below); this static table
// holds only the display label for each state code we know how to name.
// States not in this table fall through to their state_name from the
// footprint payload, so a tenant adding a new state still gets a
// readable dropdown option even before we curate a metro label for it.
// Keys are 2-letter USPS codes.
const STATE_CODE_TO_METRO_LABEL: Record<string, string> = {
  IL: 'Chicago MSA',
  TX: 'Austin',
  CA: 'SF Bay',
  CO: 'Denver',
  FL: 'Orlando',
  WA: 'Seattle',
};

// Equity thresholds expressed as a minimum equity-to-AVM ratio. We don't
// have AVM on LeadSummary (it's on Borrower360), so the predicate uses
// equity_estimate as a lower bound proxy. Good enough for the UI filter.
const EQUITY_FLOOR_USD: Record<string, number> = {
  Any: 0,
  'Equity ≥ 15%': 50_000,
  'Equity ≥ 25%': 150_000,
  'Equity ≥ 40%': 300_000,
};

// DEMOGRAPHICS replaced "Homeowner / First-time buyer / Age 55+" (no
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

// PURCHASE INTENT: wired predicates that will return zero rows until
// Cotality Building Permits + MLS Delta shares land. Copy on the filter
// calls this out to the presenter.
const PURCHASE_OPTIONS = ['All', 'Listed for sale', 'Recent permit activity', 'Both'] as const;

interface ChipFilters {
  location: string;
  demographics: string;
  lien: string;
  ownerLink: string;
  purchase: string;
  cashout: string;
}

const INITIAL_FILTERS: ChipFilters = {
  location: 'All',
  demographics: 'All',
  lien: 'Any',
  ownerLink: 'All',
  purchase: 'All',
  cashout: 'Any',
};

/**
 * Build the LOCATION-dropdown map for the current tenant footprint.
 *
 * Keeps the "All" entry first (no state filter), then one entry per
 * footprint state. Each state's display label prefers the curated metro
 * name from `STATE_CODE_TO_METRO_LABEL` (e.g. "Chicago MSA" for IL),
 * falling back to the backend's `state_name` (e.g. "Georgia") when we
 * haven't hand-curated a label yet. This makes the dropdown resilient
 * to a new tenant whose footprint includes a state we haven't picked
 * a metro for.
 */
function buildLocationToStates(
  states: ReadonlyArray<{ state_code: string; state_name: string }>,
): Record<string, string[]> {
  const out: Record<string, string[]> = { All: [] };
  for (const s of states) {
    const code = s.state_code.toUpperCase();
    const label = STATE_CODE_TO_METRO_LABEL[code] ?? s.state_name;
    out[label] = [code];
  }
  return out;
}

export default function SegmentIntelligence() {
  const [segments, setSegments] = useState<SegmentSummary[]>([]);
  const [leads, setLeads] = useState<LeadSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const footprint = useFootprint();
  const locationToStates = useMemo(
    () => buildLocationToStates(footprint.states),
    [footprint.states],
  );
  // Reload token re-runs both /api/segments and /api/leads fetches.
  // Hole-finder finding #1, 2026-04-23.
  const [reloadToken, setReloadToken] = useState<number>(0);
  const [activeSegs, setActiveSegs] = useState<string[]>(['itm']);
  const [chipFilters, setChipFilters] = useState<ChipFilters>(INITIAL_FILTERS);
  // Geography drill state emitted by USChoroplethMap. State is the 2-char
  // USPS code; null = US level (no geography filter). County/ZIP aren't
  // wired as predicates here yet (LeadSummary carries zip + city but not
  // county FIPS), but the map still exposes them on selectionChange for
  // the selection-chip UX.
  const [mapSelection, setMapSelection] = useState<MapSelection>({
    state: null,
    county: null,
    zip: null,
  });
  const handleMapSelection = useCallback((sel: MapSelection) => {
    setMapSelection(sel);
  }, []);

  useEffect(() => {
    // AbortController cancels the in-flight /api/segments request on
    // unmount or Retry. Round-2 hole-finder #10/#11, 2026-04-23.
    const ctrl = new AbortController();
    // Reset error on retry so stale error copy doesn't linger through
    // a successful reload.
    if (reloadToken > 0) setLoadError(null);
    api
      .segments(ctrl.signal)
      .then((s) => setSegments(s))
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        setLoadError(
          err instanceof Error
            ? `Couldn't load segments: ${err.message}`
            : "Couldn't load segments.",
        );
      });
    return () => {
      ctrl.abort();
    };
  }, [reloadToken]);

  useEffect(() => {
    const ctrl = new AbortController();
    api
      .leads(undefined, ctrl.signal)
      .then((l) => setLeads(l))
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        setLoadError(
          (prev) =>
            prev ??
            (err instanceof Error
              ? `Couldn't load leads: ${err.message}`
              : "Couldn't load leads."),
        );
      });
    return () => {
      ctrl.abort();
    };
  }, [reloadToken]);

  const filtered = useMemo(() => {
    let out = leads;
    if (activeSegs.length > 0) {
      out = out.filter((l) => l.segment_codes.some((s) => activeSegs.includes(s)));
    }
    // Location: restrict to selected states (if any).
    const locStates = locationToStates[chipFilters.location] ?? [];
    if (locStates.length > 0) {
      out = out.filter((l) => locStates.includes(l.state));
    }
    // DEMOGRAPHICS -> occupancy predicate (is_owner_occupied).
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
    // shares land, so these predicates will return 0 rows today. The
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
    // Geography drill: if the user picked a state on the map, restrict
    // the table to that state. ZIP is a stricter predicate so it runs
    // next when present. County isn't applied (LeadSummary doesn't carry
    // county FIPS yet); the selection chip still shows it so the user
    // sees the drill feedback.
    if (mapSelection.state) {
      out = out.filter((l) => l.state === mapSelection.state);
    }
    if (mapSelection.zip) {
      out = out.filter((l) => l.zip === mapSelection.zip);
    }
    return out;
  }, [leads, activeSegs, chipFilters, mapSelection, locationToStates]);

  const toggleSeg = (code: string) => {
    setActiveSegs((cur) => (cur.includes(code) ? cur.filter((s) => s !== code) : [...cur, code]));
  };

  const filtersDirty =
    activeSegs.length > 0 ||
    JSON.stringify(chipFilters) !== JSON.stringify(INITIAL_FILTERS) ||
    mapSelection.state !== null ||
    mapSelection.zip !== null;

  const clearAll = () => {
    setActiveSegs([]);
    setChipFilters(INITIAL_FILTERS);
    setMapSelection({ state: null, county: null, zip: null });
  };

  return (
    <PageShell
      eyebrow="Segments"
      title={
        segments.length > 0
          ? `${segments.length} borrower ${segments.length === 1 ? 'segment' : 'segments'} · select to filter`
          : 'Borrower segments · select to filter'
      }
      lede="Click segment cards to filter the ranked borrower table. Secondary filters narrow by location, demographics, lien, owner link, purchase intent, and equity. Counts refresh nightly."
      heroRight={
        filtersDirty ? (
          <Button size="sm" variant="ghost" icon="cross" onClick={clearAll}>
            Clear filters
          </Button>
        ) : undefined
      }
    >
      {loadError && (
        <div
          role="alert"
          style={{
            marginBottom: 'var(--gap-grid)',
            padding: '10px 12px',
            border: '1px solid var(--signal-danger)',
            borderRadius: 'var(--r-md)',
            color: 'var(--signal-danger)',
            fontSize: 12,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
          }}
        >
          <span>{loadError}</span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => setReloadToken((n) => n + 1)}
            aria-label="Retry loading segments and leads"
          >
            Retry
          </button>
        </div>
      )}
      {segments.length === 0 && !loadError && (
        <div className="muted body" style={{ marginBottom: 'var(--gap-grid)' }}>
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
        className="filter-row"
        style={{ marginTop: 'var(--gap-grid)' }}
        aria-label="Secondary borrower filters"
      >
        <FilterSelect
          label="LOCATION"
          value={chipFilters.location}
          options={Object.keys(locationToStates)}
          onChange={(v) => setChipFilters((f) => ({ ...f, location: v }))}
        />
        <FilterSelect
          label="DEMOGRAPHICS"
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
        <div>
          <FilterSelect
            label="PURCHASE INTENT"
            value={chipFilters.purchase}
            options={[...PURCHASE_OPTIONS]}
            onChange={(v) => setChipFilters((f) => ({ ...f, purchase: v }))}
          />
          <div
            className="filter-row__hint muted"
            style={{
              fontSize: 11,
              marginTop: 4,
              maxWidth: 220,
              lineHeight: 1.35,
            }}
          >
            Cotality MLS + Building Permits Delta shares pending — will
            return 0 until those feeds land.
          </div>
        </div>
        <FilterSelect
          label="CASH-OUT"
          value={chipFilters.cashout}
          options={Object.keys(EQUITY_FLOOR_USD)}
          onChange={(v) => setChipFilters((f) => ({ ...f, cashout: v }))}
        />
      </div>

      <div className="section-hdr">
        <div>
          <div className="eyebrow">Ranked borrowers · selected segments</div>
          <div className="h-2">
            {filtered.length} borrowers{' '}
            {activeSegs.length > 0 && (
              <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>
                · filtered by {activeSegs.join(', ')}
              </span>
            )}
          </div>
          {(mapSelection.state || mapSelection.zip) && (
            <div style={{ marginTop: 6, display: 'flex', gap: 6 }}>
              {mapSelection.state && (
                <Chip variant="neutral" icon="pin">
                  state: {mapSelection.state}
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
        <Link to="/lead-queue" className="btn">
          Deep-dive lead queue
          <Icon name="chevright" size={14} />
        </Link>
      </div>

      <div className="layoutA-grid">
        <LeadTable leads={filtered} />
        <USChoroplethMap
          height={520}
          segmentFilter={activeSegs}
          onSelectionChange={handleMapSelection}
        />
      </div>
    </PageShell>
  );
}
