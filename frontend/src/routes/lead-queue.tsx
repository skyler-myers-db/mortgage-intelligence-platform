import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import type { LeadSummary } from '../types';
import { LeadTable } from '../components/mortgage/LeadTable';

export default function LeadQueue() {
  const [searchParams] = useSearchParams();
  const segment = searchParams.get('segment') ?? undefined;
  const [leads, setLeads] = useState<LeadSummary[]>([]);

  useEffect(() => {
    api.leads(segment).then(setLeads);
  }, [segment]);

  return (
    <section className="page grid" style={{ gap: 20 }}>
      <div>
        <div className="eyebrow">Prioritized Lead Queue</div>
        <h1 className="h1">
          Ranked borrower opportunities with explainable scores
          {segment && <span className="chip" style={{ marginLeft: 12 }}>{segment}</span>}
        </h1>
      </div>
      <LeadTable leads={leads} />
    </section>
  );
}
