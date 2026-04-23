import { useEffect, useState, type CSSProperties, type ReactElement } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
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
import { Reveal } from '../components/fx/Reveal';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { segmentByCode } from '../lib/segmentMetadata';

/**
 * Borrower 360 — per-borrower dossier composed in `.surface` blocks.
 * Left column: borrower + property + Owner Link details. Middle: trigger
 * timeline. Right: Why-now panel with evidence chips + next-best-offer card
 * and forward link to the Offer Orchestrator.
 */

export default function Borrower360() {
  const { id } = useParams();
  const [b, setB] = useState<Borrower360Type | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setB(null);
    setErrorMsg(null);
    api
      .borrower(id)
      .then((data) => {
        if (!cancelled) setB(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setErrorMsg(
          err instanceof Error
            ? `Couldn't load borrower ${id}: ${err.message}`
            : `Couldn't load borrower ${id}.`,
        );
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Borrower 360 is a per-borrower detail page; without an id in the URL
  // there is no borrower to show. Send the user to the lead queue, which
  // is the source-of-truth index they can drill from. Matches the
  // product flow: portfolio → segment → lead → borrower.
  if (!id) {
    return <Navigate to="/lead-queue" replace />;
  }

  if (errorMsg) {
    return (
      <PageShell
        eyebrow="Borrower 360"
        title={`Couldn't load ${id}`}
        lede={errorMsg}
      >
        <div className="surface">
          <div className="surface__body" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <Chip variant="danger" icon="cross">Backend unavailable</Chip>
            <Link className="btn" to="/lead-queue">
              Back to lead queue
            </Link>
          </div>
        </div>
      </PageShell>
    );
  }

  if (!b) {
    return (
      <PageShell
        eyebrow="Borrower 360"
        title={<Skeleton width={280} height={30} rounded="md" />}
        lede={`Loading borrower ${id}…`}
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

  const segColor = segmentByCode(b.segment_codes[0])?.color ?? 'var(--accent)';
  // Strip any "Synthetic property · " legacy prefix so the UI reads as
  // production, not as a synthesized record. The backend's
  // subject_property field sometimes ships with this prefix; we render
  // the location only.
  const propertyAddress = b.subject_property
    .replace(/^Synthetic property\s*·\s*/i, '')
    .trim() || `${b.city}, ${b.state} ${b.zip}`;

  return (
    <PageShell
      eyebrow="Borrower 360"
      title={`Borrower ${b.borrower_id}`}
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
              <Field
                k="Property address"
                v=""
                childEl={
                  <div>
                    <div style={{ fontSize: 13, color: 'var(--text-1)' }}>{propertyAddress}</div>
                    <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>
                      Street-level address redacted for compliance; city + ZIP shown.
                    </div>
                  </div>
                }
              />
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
                      const s = segmentByCode(sid);
                      const color = s?.color ?? 'var(--accent)';
                      // Expose the segment hue via a CSS var so the stylesheet
                      // can pick the right text color per theme. Dark theme:
                      // segment hue is the text. Light theme: darker navy text
                      // (the segment hue is still visible in border + fill).
                      // Fixes WCAG 1.57/1.91/2.65:1 contrast failures flagged
                      // in the 2026-04-22 light-theme audit.
                      return (
                        <span
                          key={sid}
                          className="chip chip--segment"
                          style={
                            {
                              '--chip-hue': color,
                              background: `color-mix(in oklab, ${color} 14%, transparent)`,
                              borderColor: `color-mix(in oklab, ${color} 35%, transparent)`,
                            } as CSSProperties
                          }
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

          <Reveal>
            <div className="surface">
              <div className="surface__hdr">
                <Icon name="bolt" size={14} style={{ color: 'var(--accent)' }} />
                <div className="h-4">Trigger timeline</div>
              </div>
              <div className="surface__body">
                <TriggerTimeline events={b.trigger_timeline} segmentColor={segColor} />
              </div>
            </div>
          </Reveal>
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
                {b.why_panel.sources.map((s, idx) => {
                  // Prefer the backend-supplied human-readable label (added
                  // 2026-04-22). Fall back to the trailing UC segment so
                  // anything that hasn't been mapped still reads sensibly.
                  const label = b.why_panel.source_labels?.[idx]?.display_label
                    ?? s.split('.').slice(-1)[0];
                  return (
                    <EvidenceChip key={s} source={DRAWER_SOURCES.itm}>
                      {label}
                    </EvidenceChip>
                  );
                })}
                <EvidenceChip source={DRAWER_SOURCES.nbo}>Next-best-offer model</EvidenceChip>
                <EvidenceChip source={DRAWER_SOURCES.permit}>Building permit signal</EvidenceChip>
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
              <div className="h-4">Supporting evidence</div>
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
