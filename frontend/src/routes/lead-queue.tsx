import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { LeadSummary } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { LeadTable } from '../components/mortgage/LeadTable';
import { Chip } from '../components/Primitives';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';

/**
 * Lead Queue — deep-dive table route. Full borrower list (filtered by segment
 * URL param if present). Row expand opens the inline dossier preview.
 *
 * Honors `?state=XX`, `?zip=NNNNN`, and `?county=FFFFF` query params so the
 * home/segments geography drill can deep-link into a filtered view. The
 * state + zip filters run client-side against the already-loaded list (fast,
 * no extra fetch). The county filter resolves to the set of ZIPs within the
 * county via `/api/geo/zip-rollups?fips=FFFFF`, then intersects ZIPs — this
 * is the only predicate on LeadSummary that can express "borrowers in a
 * county" since LeadSummary carries zip but not county_fips.
 */

export default function LeadQueue() {
  const [searchParams] = useSearchParams();
  const segment = searchParams.get('segment') ?? undefined;
  // 2-char state code (e.g. `?state=IL`) from the home-map deep-link.
  // Uppercased defensively so `/lead-queue?state=il` still works.
  const stateFilter = (searchParams.get('state') ?? '').toUpperCase() || undefined;
  const zipFilter = (searchParams.get('zip') ?? '').trim() || undefined;
  const countyFilter = (searchParams.get('county') ?? '').trim() || undefined;

  // Cold-start warming-up loop — 6 retries / 5s apart. Re-runs when
  // `segment` changes (deep-link from /segment-intelligence).
  const {
    data: leadsData,
    warmingUp,
    error,
    manualRetry,
  } = useWarmingUpRetry<LeadSummary[]>(
    (signal) => api.leads(segment, signal),
    [segment],
  );
  const loading = leadsData === null && warmingUp === null && error === null;
  const loadError = error
    ? error instanceof Error
      ? `Couldn't load leads: ${error.message}`
      : "Couldn't load leads."
    : null;

  // Resolve `?county=FFFFF` → set of ZIPs via /api/geo/zip-rollups. LeadSummary
  // doesn't carry county_fips so the only way to express "borrowers in this
  // county" is to intersect on ZIP. `null` = still loading; `Set` with zero
  // entries = the county had no ZIP-level rollup (the queue will empty out
  // and the hero will show a "no ZIP rollup" chip). 2026-04-23.
  const [countyZips, setCountyZips] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (!countyFilter) {
      setCountyZips(null);
      return;
    }
    const ctrl = new AbortController();
    let cancelled = false;
    api
      .zipRollups(countyFilter, ctrl.signal)
      .then((payload) => {
        if (cancelled) return;
        setCountyZips(new Set(payload.rollups.map((r) => r.zip)));
      })
      .catch(() => {
        if (!cancelled) setCountyZips(new Set());
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [countyFilter]);

  const visibleLeads = useMemo(() => {
    let leads = leadsData ?? [];
    if (stateFilter) leads = leads.filter((l) => l.state === stateFilter);
    if (zipFilter) leads = leads.filter((l) => l.zip === zipFilter);
    if (countyFilter && countyZips) {
      // Empty set = the county returned no ZIPs; render zero rows rather
      // than silently showing all state rows. Matches user expectation
      // for "county filter was applied but the data scope is 0".
      leads = leads.filter((l) => countyZips.has(l.zip));
    }
    return leads;
  }, [leadsData, stateFilter, zipFilter, countyFilter, countyZips]);

  const countyLoading = Boolean(countyFilter) && countyZips === null;

  return (
    <PageShell
      eyebrow="Lead Queue"
      title="Ranked borrowers"
      lede="Click a row to expand the borrower preview. Approve or reject inline, or open Borrower 360 for the full dossier. Keyboard: A approves, R rejects the expanded row."
      heroRight={
        segment || stateFilter || zipFilter || countyFilter ? (
          <>
            {segment && <Chip variant="neutral">segment = {segment}</Chip>}
            {stateFilter && <Chip variant="neutral">state = {stateFilter}</Chip>}
            {zipFilter && <Chip variant="neutral">zip = {zipFilter}</Chip>}
            {countyFilter && <Chip variant="neutral">county = {countyFilter}</Chip>}
          </>
        ) : undefined
      }
    >
      {warmingUp && (
        <WarmingUpBlock state={warmingUp} title="Ranked borrowers loading" compact />
      )}
      {loadError && !warmingUp && (
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
            onClick={manualRetry}
            aria-label="Retry loading leads"
          >
            Retry
          </button>
        </div>
      )}
      {loading && !loadError && !warmingUp && (
        <div className="muted body" style={{ marginBottom: 'var(--gap-grid)' }}>
          Loading leads…
        </div>
      )}
      {countyLoading && !loading && !loadError && !warmingUp && (
        <div className="muted body" style={{ marginBottom: 'var(--gap-grid)' }}>
          Resolving county ZIPs…
        </div>
      )}
      {!loading && !loadError && !warmingUp && !countyLoading && visibleLeads.length === 0 && (
        <div className="muted body" style={{ marginBottom: 'var(--gap-grid)' }}>
          {countyFilter && countyZips && countyZips.size === 0
            ? 'No ZIP-level rollup for this county in the Cotality evaluation share.'
            : 'No leads match this filter.'}
        </div>
      )}
      <LeadTable leads={visibleLeads} />
    </PageShell>
  );
}
