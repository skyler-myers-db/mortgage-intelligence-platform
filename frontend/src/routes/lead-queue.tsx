import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import type { LeadSummary } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { LeadTable } from '../components/mortgage/LeadTable';
import { Chip } from '../components/Primitives';

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
  const [leads, setLeads] = useState<LeadSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    api
      .leads(segment)
      .then((data) => {
        if (!cancelled) {
          setLeads(data);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLeads([]);
        setLoading(false);
        setLoadError(
          err instanceof Error
            ? `Couldn't load leads: ${err.message}`
            : "Couldn't load leads.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [segment]);

  const visibleLeads = useMemo(
    () => (stateFilter ? leads.filter((l) => l.state === stateFilter) : leads),
    [leads, stateFilter],
  );

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
      {loading && !loadError && (
        <div className="muted body" style={{ marginBottom: 'var(--gap-grid)' }}>
          Loading leads…
        </div>
      )}
      {!loading && !loadError && visibleLeads.length === 0 && (
        <div className="muted body" style={{ marginBottom: 'var(--gap-grid)' }}>
          No leads match this filter.
        </div>
      )}
      <LeadTable leads={visibleLeads} />
    </PageShell>
  );
}
