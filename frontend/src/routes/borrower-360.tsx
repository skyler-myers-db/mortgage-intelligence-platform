import { useEffect, type CSSProperties, type ReactElement } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import type { Borrower360 as Borrower360Type } from '../types';
import { currency } from '../lib/formatters';
import { PageShell } from '../components/layout/PageShell';
import { TriggerTimeline } from '../components/mortgage/TriggerTimeline';
import { ScoreBadge } from '../components/mortgage/ScoreBadge';
import { ConfidenceMeter } from '../components/mortgage/ConfidenceMeter';
import { BorrowerTruthFlags } from '../components/mortgage/BorrowerTruthFlags';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { Skeleton } from '../components/ui/Skeleton';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { Reveal } from '../components/fx/Reveal';
import { descriptorFor, descriptorForEvidence } from '../lib/drawerSources';
import { segmentByCode } from '../lib/segmentMetadata';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import { useApp } from '../components/AppContext';

/**
 * Borrower 360 — per-borrower dossier composed in `.surface` blocks.
 * Left column: borrower + masked property/owner-graph refs. Middle: trigger
 * timeline. Right: Why-now panel with evidence chips + next-best-offer card
 * and forward link to the Offer Orchestrator.
 */

function titleCaseStatus(status?: string | null, fallback = 'None'): string {
  if (!status) return fallback;
  return status.split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function approvalVariant(status?: string | null): 'success' | 'danger' | 'warning' | 'neutral' {
  if (status === 'approved') return 'success';
  if (status === 'rejected') return 'danger';
  if (status === 'hold') return 'warning';
  return 'neutral';
}

function outreachVariant(status?: string | null): 'success' | 'warning' | 'neutral' {
  if (status === 'sent' || status === 'replied' || status === 'actioned') return 'success';
  if (status === 'queued' || status === 'bounced') return 'warning';
  return 'neutral';
}

function formatDateTimeShort(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export default function Borrower360() {
  const { id } = useParams();
  const { lastBorrowerId, setLastBorrowerId, saveLead, isLeadSaved } = useApp();

  useEffect(() => {
    if (id) setLastBorrowerId(id);
  }, [id, setLastBorrowerId]);

  // Cold-start posture (2026-04-23 UX fix): a fresh Databricks warehouse
  // auto-suspends, so the first nav to /borrower-360/{id} commonly
  // returns 503 {retryable:true, dependency:"warehouse"} while it warms
  // up. Instead of flashing a red "Backend unavailable" banner, the
  // warming-up retry hook retries up to 6× over 30s and drives the
  // WarmingUpBlock "Warehouse warming up (attempt N of 6)…" copy. Any
  // non-retryable error (404, 500) falls through to the persistent
  // error path with the existing "Back to lead queue" CTA.
  const { data: b, warmingUp, error, manualRetry } = useWarmingUpRetry<Borrower360Type>(
    (signal) => api.borrower(id!, signal),
    [id],
    { enabled: Boolean(id) },
  );

  // Borrower 360 is a per-borrower detail page; without an id in the URL
  // there is no borrower to show. Render a proper empty-state landing
  // page instead of silently redirecting — clicking the tab should not
  // feel like a broken link.
  if (!id && lastBorrowerId) {
    return <Navigate to={`/borrower-360/${lastBorrowerId}`} replace />;
  }

  if (!id) {
    return (
      <PageShell
        eyebrow="Borrower 360"
        title="Choose a borrower to inspect"
        lede="Borrower 360 shows a single borrower's full dossier — masked property ref, equity estimate, rate spread, trigger timeline, recommended offer, and every evidence chip that justifies the score. Pick a borrower from the Lead Queue to open it here."
        heroRight={
          <Link className="btn btn--primary" to="/lead-queue">
            Browse lead queue
            <Icon name="chevright" size={14} />
          </Link>
        }
      >
        <div className="surface">
          <div className="surface__hdr">
            <Icon name="user" size={14} className="icon-accent" />
            <div className="h-4">What you'll see</div>
          </div>
          <div className="surface__body surface__body--stack-sm">
            <div className="chip-row">
              <Chip variant="neutral" icon="user">Customer 360</Chip>
              <Chip variant="neutral" icon="bolt">Trigger timeline</Chip>
              <Chip variant="neutral" icon="shield">Why-now rationale</Chip>
              <Chip variant="neutral" icon="layers">Supporting evidence</Chip>
            </div>
            <p className="body muted flush">
              Every score traces back to a Cotality source row via the evidence
              chips. Open a borrower to see the full dossier.
            </p>
          </div>
        </div>
      </PageShell>
    );
  }

  // Warming-up takes priority over `error` — `useWarmingUpRetry` never
  // sets both at once, but this ordering makes the intent explicit.
  if (warmingUp) {
    return (
      <PageShell
        eyebrow={warmingUp.label}
        title={`Loading ${id}…`}
        lede="Databricks SQL warehouses auto-suspend when idle. It takes ~30 seconds to warm up. Retrying automatically…"
      >
        <WarmingUpBlock state={warmingUp} title={`Loading borrower ${id}`} />
      </PageShell>
    );
  }

  if (error) {
    const notFound = error instanceof ApiError && error.status === 404;
    const errorLede = notFound
      ? `Borrower ${id} was not found. Check the ID, use search, or return to the lead queue.`
      : `Couldn't load borrower ${id}: ${error.message}`;
    return (
      <PageShell
        eyebrow="Borrower 360"
        title={notFound ? `Borrower ${id} not found` : `Couldn't load ${id}`}
        lede={errorLede}
      >
        <div className="surface">
          <div className="surface__body surface__body--inline">
            <Chip variant={notFound ? 'warning' : 'danger'} icon={notFound ? 'search' : 'cross'}>
              {notFound ? 'Not found' : 'Backend unavailable'}
            </Chip>
            {!notFound && (
              <Button onClick={manualRetry} aria-label={`Retry loading borrower ${id}`}>
                Retry
              </Button>
            )}
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
          <div className="stack-grid">
            <div className="surface">
              <div className="surface__hdr">
                <Skeleton width={28} height={28} rounded="md" />
                <Skeleton width={140} height={14} rounded="sm" />
              </div>
              <div className="surface__body surface__body--grid-2">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div key={i}>
                    <div className="mb-2">
                      <Skeleton width={80} height={11} rounded="sm" />
                    </div>
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
              <div className="surface__body stack-md">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="stack-sm">
                    <Skeleton width={90} height={10} rounded="sm" />
                    <Skeleton width="70%" height={13} rounded="sm" />
                    <Skeleton width="55%" height={12} rounded="sm" />
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="stack-grid">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="surface">
                <div className="surface__hdr">
                  <Skeleton width={16} height={16} rounded="sm" />
                  <Skeleton width={160} height={14} rounded="sm" />
                </div>
                <div className="surface__body surface__body--stack-sm">
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
  const saved = isLeadSaved(b.borrower_id);
  const saveCurrentLead = () => {
    saveLead({
      borrower_id: b.borrower_id,
      city: b.city,
      state: b.state,
      zip: b.zip,
      recommended_offer: b.recommended_offer,
      opportunity_score: b.opportunity_score,
      confidence: b.confidence,
    });
  };

  return (
    <PageShell
      eyebrow="Borrower 360"
      title={`Borrower ${b.borrower_id}`}
      lede={`${b.city}, ${b.state} ${b.zip} · ${b.recommended_offer}`}
      heroRight={
        <>
          <ScoreBadge value={b.opportunity_score} />
          <ConfidenceMeter value={b.confidence} />
          <Chip variant={approvalVariant(b.approval_status)}>
            Approval {titleCaseStatus(b.approval_status, 'Pending')}
          </Chip>
          <Chip variant={outreachVariant(b.outreach_status)}>
            Outreach {titleCaseStatus(b.outreach_status)}
          </Chip>
        </>
      }
    >
      <div className="layoutA-grid">
        {/* Left column — Customer 360 + trigger timeline stacked */}
        <div className="stack-grid">
          <div className="surface">
            <div className="surface__hdr">
              <div className="surface__icon">
                <Icon name="user" size={14} />
              </div>
              <div className="h-4">Customer 360</div>
            </div>
            <div className="surface__body field-grid">
              <Field k="Property ref" v={b.clip_id} mono />
              <Field k="Owner graph ref" v={b.owner_link_id} mono />
              <Field
                k="Property address"
                v=""
                childEl={
                  <div>
                    <div className="field__value">{propertyAddress}</div>
                    <div className="field__sub">
                      Street-level address redacted for compliance; city + ZIP shown.
                    </div>
                  </div>
                }
              />
              <Field k="AVM" v={currency(b.avm_value)} mono />
              <Field k="Current lien" v={`${currency(b.current_lien_balance)} · ${b.current_rate}%`} mono />
              <Field k="LTV / Equity" v={`${b.ltv}% · ${currency(b.equity_estimate)}`} mono />
              <Field k="Related properties" v={`${b.related_property_count} (via owner graph)`} />
              <Field
                k="Relationship flags"
                v=""
                childEl={<BorrowerTruthFlags borrower={b} />}
              />
              <Field k="Assigned to" v={b.assigned_to_label ?? b.assigned_to_email ?? 'Unassigned'} />
              <Field k="Approval status" v={titleCaseStatus(b.approval_status, 'Pending')} />
              <Field
                k="Outreach status"
                v={`${titleCaseStatus(b.outreach_status)} · ${formatDateTimeShort(b.outreach_at)}`}
              />
              <Field
                k="Latest disposition"
                v={`${titleCaseStatus(b.latest_disposition_outcome, 'Untouched')} · ${formatDateTimeShort(b.latest_disposition_at)}`}
              />
              <Field
                k="Segments"
                v=""
                childEl={
                  <div className="chip-row mt-1">
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
                <Icon name="bolt" size={14} className="icon-accent" />
                <div className="h-4">Trigger timeline</div>
              </div>
              <div className="surface__body">
                <TriggerTimeline events={b.trigger_timeline} segmentColor={segColor} />
              </div>
            </div>
          </Reveal>
        </div>

        {/* Right column — Why-now + NBO + CTA */}
        <div className="stack-grid">
          <div className="surface">
            <div className="surface__hdr">
              <Icon name="shield" size={14} className="icon-accent" />
              <div className="h-4">Why we recommend this</div>
            </div>
            <div className="surface__body">
              <div className="chip-row mb-3">
                <Chip variant={b.why_panel.in_the_money ? 'success' : 'warning'}>
                  {b.why_panel.in_the_money ? 'In-the-money' : 'Not in the money'}
                </Chip>
                <span className="mono num text-1">
                  +{b.why_panel.rate_spread_bps} bps
                </span>
                <span className="muted fs-12">
                  vs. par {(b.why_panel.market_rate * 100).toFixed(3)}%
                </span>
              </div>
              <div className="rationale-box">
                <span className="text-1 fw-500">Rationale.</span>{' '}
                {b.why_panel.in_the_money_reason}
              </div>
              <div className="chip-row mt-3">
                <span className="muted fs-11">Evidence:</span>
                {b.why_panel.sources.map((s, idx) => {
                  // Prefer the backend-supplied human-readable label (added
                  // 2026-04-22). Fall back to the trailing UC segment so
                  // anything that hasn't been mapped still reads sensibly.
                  const label = b.why_panel.source_labels?.[idx]?.display_label
                    ?? s.split('.').slice(-1)[0];
                  // Route each chip to the drawer entry matching its UC
                  // source. Previously every why-panel chip opened the
                  // in-the-money drawer, which was a parity bug when the
                  // source was NBO/permit/population.
                  return (
                    <EvidenceChip key={s} source={descriptorFor(s)}>
                      {label}
                    </EvidenceChip>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="surface">
            <div className="surface__hdr">
              <Icon name="bolt" size={14} className="icon-accent" />
              <div className="h-4">Next-best-offer</div>
            </div>
            <div className="surface__body">
              <div className="split-row">
                <div className="offer-title">{b.recommended_offer}</div>
                <ScoreBadge value={b.opportunity_score} />
              </div>
              <p className="body mt-2">{b.why_now}</p>
              <div className="chip-row mt-3">
                <Link
                  className="btn btn--primary"
                  to={`/offer-orchestrator/${b.borrower_id}`}
                  onClick={() => setLastBorrowerId(b.borrower_id)}
                >
                  Build outreach draft
                  <Icon name="chevright" size={14} />
                </Link>
                <Button
                  variant={saved ? 'ghost' : 'default'}
                  size="default"
                  icon={saved ? 'check' : 'tag'}
                  onClick={saveCurrentLead}
                  aria-label={`${saved ? 'Saved' : 'Save'} borrower ${b.borrower_id}`}
                >
                  {saved ? 'Saved' : 'Save lead'}
                </Button>
              </div>
            </div>
          </div>

          <div className="surface">
            <div className="surface__hdr">
              <Icon name="layers" size={14} className="icon-accent" />
              <div className="h-4">Supporting evidence</div>
            </div>
            <div className="surface__body surface__body--stack-sm">
              {b.evidence_events.map((e) => (
                <div key={e.evidence_id} className="chip-row chip-row--baseline">
                  <EvidenceChip source={descriptorForEvidence(e)}>{e.source_product}</EvidenceChip>
                  <span className="text-2 fs-13">{e.display_text}</span>
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
      <div className="field__label">{k}</div>
      {childEl ?? <div className={`field__value ${mono ? 'mono num' : ''}`}>{v}</div>}
    </div>
  );
}
