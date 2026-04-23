import { useMemo } from 'react';
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
 * Also honors `?state=XX` for the home-page map drill-through. The predicate
 * is applied client-side on the already-loaded lead list so the same fetch
 * powers both filtered and unfiltered views.
 */

export default function LeadQueue() {
  const [searchParams] = useSearchParams();
  const segment = searchParams.get('segment') ?? undefined;
  // 2-char state code (e.g. `?state=IL`) from the home-map deep-link.
  // Uppercased defensively so `/lead-queue?state=il` still works.
  const stateFilter = (searchParams.get('state') ?? '').toUpperCase() || undefined;

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

  const visibleLeads = useMemo(() => {
    const leads = leadsData ?? [];
    return stateFilter ? leads.filter((l) => l.state === stateFilter) : leads;
  }, [leadsData, stateFilter]);

  return (
    <PageShell
      eyebrow="Lead Queue"
      title="Ranked borrowers"
      lede="Click a row to expand the borrower preview. Approve or reject inline, or open Borrower 360 for the full dossier. Keyboard: A approves, R rejects the expanded row."
      heroRight={
        segment || stateFilter ? (
          <>
            {segment && <Chip variant="neutral">segment = {segment}</Chip>}
            {stateFilter && <Chip variant="neutral">state = {stateFilter}</Chip>}
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
      {!loading && !loadError && !warmingUp && visibleLeads.length === 0 && (
        <div className="muted body" style={{ marginBottom: 'var(--gap-grid)' }}>
          No leads match this filter.
        </div>
      )}
      <LeadTable leads={visibleLeads} />
    </PageShell>
  );
}
