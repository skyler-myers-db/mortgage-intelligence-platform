import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import type { LeadSummary, SegmentSummary } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { SegmentCard } from '../components/mortgage/SegmentCard';
import { LeadTable } from '../components/mortgage/LeadTable';
import { USChoroplethMap } from '../components/mortgage/USChoroplethMap';
import { Button, Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { FilterSelect } from '../components/ui/FilterSelect';

/**
 * Segment Intelligence — prototype composition: segment cards across the top
 * as a filter grid, ranked-borrower table below on the left, map / summary
 * preview on the right. This is the densest Module 0 screen and lines up 1:1
 * with the prototype's "segment-first" layout.
 */

// Map display-label -> US state code (matches LeadSummary.state). Keeps the
// options list readable ("Chicago MSA") while the predicate logic works on
// 2-letter state codes. The "MSA" entries are approximations — presenters
// call out a metro; the filter drops to state-level for now.
// Slice 9: dropped Atlanta / Nashville and promoted Chicago MSA to match
// the 6-state Delta Share footprint (IL / CA / FL / TX / WA / CO).
// TODO: wire to backend when MSA / county rollups land.
const LOCATION_TO_STATES: Record<string, string[]> = {
  All: [],
  'Chicago MSA': ['IL'],
  Austin: ['TX'],
  'SF Bay': ['CA'],
  Denver: ['CO'],
  Orlando: ['FL'],
  Seattle: ['WA'],
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

export default function SegmentIntelligence() {
  const [segments, setSegments] = useState<SegmentSummary[]>([]);
  const [leads, setLeads] = useState<LeadSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [activeSegs, setActiveSegs] = useState<string[]>(['itm']);
  const [chipFilters, setChipFilters] = useState<ChipFilters>(INITIAL_FILTERS);

  useEffect(() => {
    let cancelled = false;
    api
      .segments()
      .then((s) => {
        if (!cancelled) setSegments(s);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLoadError(
            err instanceof Error
              ? `Couldn't load segments: ${err.message}`
              : "Couldn't load segments.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .leads()
      .then((l) => {
        if (!cancelled) setLeads(l);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLoadError(
            (prev) =>
              prev ??
              (err instanceof Error
                ? `Couldn't load leads: ${err.message}`
                : "Couldn't load leads."),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Filters that have no matching borrower signal (demographics, owner-link,
  // purchase intent) — used to dim the table so the presenter can signal
  // intent without hiding rows.
  const hasSoftFilter =
    chipFilters.demographics !== 'All' ||
    chipFilters.ownerLink !== 'All' ||
    chipFilters.purchase !== 'All' ||
    chipFilters.lien !== 'Any';

  const filtered = useMemo(() => {
    let out = leads;
    if (activeSegs.length > 0) {
      out = out.filter((l) => l.segment_codes.some((s) => activeSegs.includes(s)));
    }
    // Location: restrict to selected states (if any).
    const locStates = LOCATION_TO_STATES[chipFilters.location] ?? [];
    if (locStates.length > 0) {
      out = out.filter((l) => locStates.includes(l.state));
    }
    // Cash-out equity floor.
    const floor = EQUITY_FLOOR_USD[chipFilters.cashout] ?? 0;
    if (floor > 0) {
      out = out.filter((l) => l.equity_estimate >= floor);
    }
    // TODO: wire to backend when richer borrower signals land
    // (demographics / owner-link / purchase-intent / lien-status).
    return out;
  }, [leads, activeSegs, chipFilters]);

  const toggleSeg = (code: string) => {
    setActiveSegs((cur) => (cur.includes(code) ? cur.filter((s) => s !== code) : [...cur, code]));
  };

  const filtersDirty =
    activeSegs.length > 0 || JSON.stringify(chipFilters) !== JSON.stringify(INITIAL_FILTERS);

  const clearAll = () => {
    setActiveSegs([]);
    setChipFilters(INITIAL_FILTERS);
  };

  return (
    <PageShell
      eyebrow="Segment Intelligence"
      title={
        segments.length > 0
          ? `${segments.length} borrower ${segments.length === 1 ? 'segment' : 'segments'} · select to filter`
          : 'Borrower segments · select to filter'
      }
      lede="Every segment is defined by a rule in Unity Catalog. Counts refresh nightly from Cotality public records + lien + market + listing + permit + AVM feeds."
      heroRight={
        <>
          <Chip variant="neutral" icon="db">Refreshed 06:12 UTC · Delta Share</Chip>
          {filtersDirty && (
            <Button size="sm" variant="ghost" icon="cross" onClick={clearAll}>
              Clear filters
            </Button>
          )}
        </>
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
          }}
        >
          {loadError}
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
          options={Object.keys(LOCATION_TO_STATES)}
          onChange={(v) => setChipFilters((f) => ({ ...f, location: v }))}
        />
        <FilterSelect
          label="DEMOGRAPHICS"
          value={chipFilters.demographics}
          options={['All', 'Owner-occupied', 'Investor', 'Second home']}
          onChange={(v) => setChipFilters((f) => ({ ...f, demographics: v }))}
        />
        <FilterSelect
          label="LIEN"
          value={chipFilters.lien}
          options={['Any', 'Open 1st lien', 'Open HELOC', 'Free & clear']}
          onChange={(v) => setChipFilters((f) => ({ ...f, lien: v }))}
        />
        <FilterSelect
          label="OWNER LINK"
          value={chipFilters.ownerLink}
          options={['All', 'Single property', 'Multi-property']}
          onChange={(v) => setChipFilters((f) => ({ ...f, ownerLink: v }))}
        />
        <FilterSelect
          label="PURCHASE INTENT"
          value={chipFilters.purchase}
          options={['All', 'Listed for sale', 'Permit activity']}
          onChange={(v) => setChipFilters((f) => ({ ...f, purchase: v }))}
        />
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
        </div>
        <Link to="/lead-queue" className="btn">
          Deep-dive lead queue
          <Icon name="chevright" size={14} />
        </Link>
      </div>

      <div
        className="layoutA-grid"
        style={hasSoftFilter ? { opacity: 0.72, transition: 'opacity var(--dur-fast) var(--ease)' } : undefined}
      >
        <LeadTable leads={filtered} />
        <USChoroplethMap height={520} segmentFilter={activeSegs} />
      </div>
    </PageShell>
  );
}
