import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import type { LeadSummary } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { LeadTable } from '../components/mortgage/LeadTable';
import { Chip } from '../components/Primitives';

/**
 * Lead Queue — deep-dive table route. Full borrower list (filtered by segment
 * URL param if present). Row expand opens the inline dossier preview.
 */

export default function LeadQueue() {
  const [searchParams] = useSearchParams();
  const segment = searchParams.get('segment') ?? undefined;
  const [leads, setLeads] = useState<LeadSummary[]>([]);

  useEffect(() => {
    api.leads(segment).then(setLeads);
  }, [segment]);

  return (
    <PageShell
      eyebrow="Prioritized Lead Queue"
      title="Ranked borrower opportunities with explainable scores"
      lede="Every row carries an opportunity score, confidence meter, and evidence chip. Click a row to expand a borrower dossier preview; open the full Borrower 360 for the Why panel and trigger timeline."
      heroRight={
        <>
          <Chip variant="neutral" icon="db">mip.gold.lead_scores</Chip>
          {segment && <Chip variant="neutral">segment = {segment}</Chip>}
        </>
      }
    >
      <LeadTable leads={leads} />
    </PageShell>
  );
}
