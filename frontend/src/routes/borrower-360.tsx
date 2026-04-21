import { useEffect, useState, type ReactElement } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../lib/api';
import type { Borrower360 as Borrower360Type } from '../types';
import { currency } from '../lib/formatters';
import { PageShell } from '../components/layout/PageShell';
import { TriggerTimeline } from '../components/mortgage/TriggerTimeline';
import { ScoreBadge } from '../components/mortgage/ScoreBadge';
import { ConfidenceMeter } from '../components/mortgage/ConfidenceMeter';
import { Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { Skeleton } from '../components/ui/Skeleton';
import { DRAWER_SOURCES, mockSegments } from '../mocks/demoData';

/**
 * Borrower 360 — public-record dossier composed in `.surface` blocks.
 * Left column: borrower + property + Owner Link details. Middle: trigger
 * timeline. Right: Why-now panel with evidence chips + next-best-offer card
 * and forward link to the Offer Orchestrator.
 */

export default function Borrower360() {
  const { id = 'B-48291' } = useParams();
  const [b, setB] = useState<Borrower360Type | null>(null);

  useEffect(() => {
    api.borrower(id).then(setB);
  }, [id]);

  if (!b) {
    return (
      <PageShell
        eyebrow="Borrower 360 · Public-Record Dossier"
        title={<Skeleton width={280} height={30} rounded="md" />}
        lede={`Fetching public-record Customer 360 for ${id}…`}
      >
        <div className="layoutA-grid">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--gap-grid)' }}>
            <div className="surface">
              <div className="surface__hdr">
                <Skeleton width={28} height={28} rounded="md" />
                <Skeleton width={140} height={14} rounded="sm" />
              </div>
              <div className="surface__body" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                {Array.from({ length: 8 }).map((_, i) => (
                  <div key={i}>
                    <Skeleton width={80} height={11} rounded="sm" style={{ marginBottom: 6 }} />
                    <Skeleton width="85%" height={14} rounded="sm" />
                  </div>
                ))}
              </div>
            </div>
            <div className="surface">
              <div className="surface__hdr">
                <Skeleton width={16} height={16} rounded="sm" />
                <Skeleton width={140} height={14} rounded="sm" />
              </div>
              <div className="surface__body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <Skeleton width={90} height={10} rounded="sm" />
                    <Skeleton width="70%" height={13} rounded="sm" />
                    <Skeleton width="55%" height={12} rounded="sm" />
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--gap-grid)' }}>
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="surface">
                <div className="surface__hdr">
                  <Skeleton width={16} height={16} rounded="sm" />
                  <Skeleton width={160} height={14} rounded="sm" />
                </div>
                <div className="surface__body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <Skeleton width="90%" height={14} rounded="sm" />
                  <Skeleton width="80%" height={12} rounded="sm" />
                  <Skeleton width="65%" height={12} rounded="sm" />
                </div>
              </div>
            ))}
          </div>
        </div>
      </PageShell>
    );
  }

  const segColor = mockSegments.find((s) => s.code === b.segment_codes[0])?.color ?? 'var(--accent)';

  return (
    <PageShell
      eyebrow="Borrower 360 · Public-Record Dossier"
      title={b.display_name}
      lede={`${b.city}, ${b.state} ${b.zip} · ${b.recommended_offer}`}
      heroRight={
        <>
          <ScoreBadge value={b.opportunity_score} />
          <ConfidenceMeter value={b.confidence} />
          <Chip variant="warning">Approval pending</Chip>
        </>
      }
    >
      <div className="layoutA-grid">
        {/* Left column — Customer 360 + trigger timeline stacked */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--gap-grid)' }}>
          <div className="surface">
            <div className="surface__hdr">
              <div
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 8,
                  background: 'var(--accent-soft)',
                  color: 'var(--accent)',
                  display: 'grid',
                  placeItems: 'center',
                }}
              >
                <Icon name="user" size={14} />
              </div>
              <div className="h-4">Customer 360</div>
            </div>
            <div className="surface__body" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              <Field k="CLIP" v={b.clip_id} mono />
              <Field k="Owner Link" v={b.owner_link_id} mono />
              <Field k="Subject property" v={b.subject_property} />
              <Field k="AVM" v={currency(b.avm_value)} mono />
              <Field k="Current lien" v={`${currency(b.current_lien_balance)} · ${b.current_rate}%`} mono />
              <Field k="LTV / Equity" v={`${b.ltv}% · ${currency(b.equity_estimate)}`} mono />
              <Field k="Related properties" v={`${b.related_property_count} (via Owner Link)`} />
              <Field
                k="Segments"
                v=""
                childEl={
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
                    {b.segment_codes.map((sid) => {
                      const s = mockSegments.find((x) => x.code === sid);
                      const color = s?.color ?? 'var(--accent)';
                      return (
                        <span
                          key={sid}
                          className="chip"
                          style={{
                            color,
                            background: `color-mix(in oklab, ${color} 14%, transparent)`,
                            borderColor: `color-mix(in oklab, ${color} 35%, transparent)`,
                          }}
                        >
                          {s?.name ?? sid}
                        </span>
                      );
                    })}
                  </div>
                }
              />
            </div>
          </div>

          <div className="surface">
            <div className="surface__hdr">
              <Icon name="bolt" size={14} style={{ color: 'var(--accent)' }} />
              <div className="h-4">Trigger timeline</div>
            </div>
            <div className="surface__body">
              <TriggerTimeline events={b.trigger_timeline} segmentColor={segColor} />
            </div>
          </div>
        </div>

        {/* Right column — Why-now + NBO + CTA */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--gap-grid)' }}>
          <div className="surface">
            <div className="surface__hdr">
              <Icon name="shield" size={14} style={{ color: 'var(--accent)' }} />
              <div className="h-4">Why we recommend this</div>
            </div>
            <div className="surface__body">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <Chip variant={b.why_panel.in_the_money ? 'success' : 'warning'}>
                  {b.why_panel.in_the_money ? 'In-the-money' : 'Not in the money'}
                </Chip>
                <span className="mono num" style={{ color: 'var(--text-1)' }}>
                  +{b.why_panel.rate_spread_bps} bps
                </span>
                <span className="muted" style={{ fontSize: 12 }}>
                  vs. par {(b.why_panel.market_rate * 100).toFixed(3)}%
                </span>
              </div>
              <div style={{ padding: '10px 12px', background: 'var(--bg-3)', borderRadius: 6, fontSize: 13, color: 'var(--text-2)' }}>
                <span style={{ color: 'var(--text-1)', fontWeight: 500 }}>Rationale.</span>{' '}
                {b.why_panel.in_the_money_reason}
              </div>
              <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                <span className="muted" style={{ fontSize: 11 }}>Evidence:</span>
                {b.why_panel.sources.map((s) => (
                  <EvidenceChip key={s} source={DRAWER_SOURCES.itm}>
                    {s.split('.').slice(-1)[0]}
                  </EvidenceChip>
                ))}
                <EvidenceChip source={DRAWER_SOURCES.nbo}>mlflow.mtg_nbo_v3</EvidenceChip>
                <EvidenceChip source={DRAWER_SOURCES.permit}>permits.building</EvidenceChip>
              </div>
            </div>
          </div>

          <div className="surface">
            <div className="surface__hdr">
              <Icon name="bolt" size={14} style={{ color: 'var(--accent)' }} />
              <div className="h-4">Next-best-offer</div>
            </div>
            <div className="surface__body">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em' }}>{b.recommended_offer}</div>
                <ScoreBadge value={b.opportunity_score} />
              </div>
              <p className="body" style={{ marginTop: 8 }}>{b.why_now}</p>
              <div style={{ marginTop: 12 }}>
                <Link className="btn btn--primary" to={`/offer-orchestrator/${b.borrower_id}`}>
                  Build outreach draft
                  <Icon name="chevright" size={14} />
                </Link>
              </div>
            </div>
          </div>

          <div className="surface">
            <div className="surface__hdr">
              <Icon name="layers" size={14} style={{ color: 'var(--accent)' }} />
              <div className="h-4">Why we trust this</div>
            </div>
            <div className="surface__body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {b.evidence_events.map((e) => (
                <div key={e.evidence_id} style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                  <EvidenceChip source={DRAWER_SOURCES.itm}>{e.source_product}</EvidenceChip>
                  <span style={{ color: 'var(--text-2)', fontSize: 13 }}>{e.display_text}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </PageShell>
  );
}

function Field({ k, v, mono, childEl }: { k: string; v: string; mono?: boolean; childEl?: ReactElement }) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 11 }}>{k}</div>
      {childEl ?? <div className={mono ? 'mono num' : ''} style={{ fontSize: 13, color: 'var(--text-1)' }}>{v}</div>}
    </div>
  );
}
