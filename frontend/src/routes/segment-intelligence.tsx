import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import type { SegmentSummary } from '../types';
import { SegmentCard } from '../components/mortgage/SegmentCard';
import { MapPlaceholder } from '../components/mortgage/MapPlaceholder';

export default function SegmentIntelligence() {
  const [segments, setSegments] = useState<SegmentSummary[]>([]);
  const [active, setActive] = useState<string>('itm');
  const navigate = useNavigate();

  useEffect(() => {
    api.segments().then(setSegments);
  }, []);

  return (
    <section className="page grid" style={{ gap: 20 }}>
      <div>
        <div className="eyebrow">Segment Intelligence</div>
        <h1 className="h1">Six borrower segments · select to filter</h1>
      </div>
      <div className="grid grid-6">
        {segments.map((s) => (
          <SegmentCard
            key={s.code}
            segment={s}
            active={s.code === active}
            onClick={() => {
              setActive(s.code);
              navigate(`/lead-queue?segment=${s.code}`);
            }}
          />
        ))}
      </div>
      <div className="grid grid-2">
        <MapPlaceholder />
        <div className="card card-body">
          <h2 className="h2">Why this matters</h2>
          <p className="muted">
            Cotality market activity shows where borrower intent is rising. Owner Link and CLIP connect property, owner,
            related properties, and lien history so the lender can act before the lead enters CRM.
          </p>
          <Link className="button primary" to={`/lead-queue?segment=${active}`}>View ranked leads</Link>
        </div>
      </div>
    </section>
  );
}
