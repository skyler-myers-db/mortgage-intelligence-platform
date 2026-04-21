import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import type { LeadSummary, SegmentSummary } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { SegmentCard } from '../components/mortgage/SegmentCard';
import { LeadTable } from '../components/mortgage/LeadTable';
import { MapPlaceholder } from '../components/mortgage/MapPlaceholder';
import { Button, Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';

/**
 * Segment Intelligence — prototype composition: segment cards across the top
 * as a filter grid, ranked-borrower table below on the left, map / summary
 * preview on the right. This is the densest Module 0 screen and lines up 1:1
 * with the prototype's "segment-first" layout.
 */

export default function SegmentIntelligence() {
  const [segments, setSegments] = useState<SegmentSummary[]>([]);
  const [leads, setLeads] = useState<LeadSummary[]>([]);
  const [activeSegs, setActiveSegs] = useState<string[]>(['itm']);

  useEffect(() => {
    api.segments().then(setSegments);
  }, []);

  useEffect(() => {
    api.leads().then(setLeads);
  }, []);

  const filtered = useMemo(() => {
    if (activeSegs.length === 0) return leads;
    return leads.filter((l) => l.segment_codes.some((s) => activeSegs.includes(s)));
  }, [leads, activeSegs]);

  const toggleSeg = (code: string) => {
    setActiveSegs((cur) => (cur.includes(code) ? cur.filter((s) => s !== code) : [...cur, code]));
  };

  return (
    <PageShell
      eyebrow="Segment Intelligence"
      title="Six borrower segments · select to filter"
      lede="Every segment is defined by a rule in Unity Catalog. Counts refresh nightly from Cotality public records + lien + market + listing + permit + AVM feeds."
      heroRight={
        <>
          <Chip variant="neutral" icon="db">Refreshed 06:12 UTC · Delta Share</Chip>
          {activeSegs.length > 0 && (
            <Button size="sm" variant="ghost" icon="cross" onClick={() => setActiveSegs([])}>
              Clear filters
            </Button>
          )}
        </>
      }
    >
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

      <div className="layoutA-grid">
        <LeadTable leads={filtered} />
        <MapPlaceholder height={520} />
      </div>
    </PageShell>
  );
}
