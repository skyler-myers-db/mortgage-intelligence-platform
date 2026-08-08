import { useEffect, useState, type CSSProperties, type ReactElement, type ReactNode } from 'react';
import { Link, Navigate, useParams } from 'react-router';
import { api, ApiError } from '../lib/api';
import type { Borrower360 as Borrower360Type } from '../types';
import { currency, ratePct, ratePctFromFraction, signedBpsLabel } from '../lib/formatters';
import { PageShell } from '../components/layout/PageShell';
import { TriggerTimeline } from '../components/mortgage/TriggerTimeline';
import { BorrowerStoryCard } from '../components/mortgage/BorrowerStoryCard';
import { ScoreBadge } from '../components/mortgage/ScoreBadge';
import { ConfidenceMeter } from '../components/mortgage/ConfidenceMeter';
import { BorrowerTruthFlags } from '../components/mortgage/BorrowerTruthFlags';
import { BorrowerProofDrawer } from '../components/mortgage/BorrowerProofDrawer';
import { TopLeadsQuickPick } from '../components/mortgage/TopLeadsQuickPick';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { GlossaryTerm } from '../components/GlossaryTerm';
import { Icon } from '../components/Icon';
import { Skeleton } from '../components/ui/Skeleton';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { Reveal } from '../components/fx/Reveal';
import { descriptorFor, descriptorForEvidence } from '../lib/drawerSources';
import { offerDisplayLabel, offerRationale, offerShortDescription } from '../lib/offerLanguage';
import { safeSegmentName, segmentByCode } from '../lib/segmentMetadata';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import { queryKeys } from '../lib/queryKeys';
import { useApp } from '../components/AppContext';

/**
 * Borrower 360 — per-borrower dossier composed in `.surface` blocks.
 * Left column: borrower + masked property/owner-graph refs. Middle: trigger
 * timeline. Right: Why-now panel with evidence chips + next-best-offer card
 * and forward link to the Offer Orchestrator.
 */

export const BORROWER_DOSSIER_LABEL = 'Borrower dossier';

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
  const [proofOpen, setProofOpen] = useState(false);

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
    { enabled: Boolean(id), queryKey: queryKeys.borrower(id) },
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
        lede="Borrower 360 shows a single borrower's full dossier — masked property ref, equity estimate, rate spread, trigger timeline, primary offer, and every evidence chip that justifies the score. Pick a borrower from the Lead Queue to open it here."
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
              <Chip variant="neutral" icon="user">{BORROWER_DOSSIER_LABEL}</Chip>
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
        <TopLeadsQuickPick basePath="/borrower-360" />
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
  const hasAvm = b.avm_value > 0;
  const firstPartySignalSummary = [
    b.has_first_party_relationship ? `${b.first_party_relationship_depth ?? 0} first-party links` : null,
    (b.first_party_recent_interactions ?? 0) > 0
      ? `${b.first_party_recent_interactions} recent interactions`
      : null,
    b.first_party_recent_application ? 'Recent application' : null,
  ].filter(Boolean);
  const saved = isLeadSaved(b.borrower_id);
  const lienBandLow = b.current_lien_balance_low ?? b.current_lien_balance;
  const lienBandHigh = b.current_lien_balance_high ?? b.current_lien_balance;
  const lienBandLabel = `${currency(lienBandLow)}-${currency(lienBandHigh)}`;
  const estimatedUpbBandSource = descriptorFor('fn_estimated_upb_confidence_band');
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

  const offerLabel = offerDisplayLabel(b.recommended_offer_code, b.recommended_offer);
  const offerDescription = offerShortDescription(b.recommended_offer_code);

  return (
    <PageShell
      eyebrow="Borrower 360"
      title={`Borrower ${b.borrower_id}`}
      lede={`${b.city}, ${b.state} ${b.zip} · ${offerLabel}`}
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
        {/* Left column — Borrower dossier + trigger timeline stacked */}
        <div className="stack-grid">
          {/* "Tell the story" (Buyer-Wow #3): the system explains the lead in
              plain English, grounded + numerically verified against this
              dossier. Sits above the raw dossier so the narrative frames it. */}
          <BorrowerStoryCard borrower={b} />
          <div className="surface">
            <div className="surface__hdr surface__hdr--split">
              <div className="surface__hdr-main">
                <div className="surface__icon">
                  <Icon name="user" size={14} />
                </div>
                <div className="h-4">{BORROWER_DOSSIER_LABEL}</div>
              </div>
              {b.first_party_synthetic_demo && (
                <span
                  className="muted fs-11"
                  title="This borrower's first-party fields are synthetic demo data (Summit Mortgage demo tenant)."
                >
                  Summit demo synthetic
                </span>
              )}
            </div>
            <div className="surface__body field-grid">
              <Field k={<GlossaryTerm term="clip">Property ref</GlossaryTerm>} v={b.clip_id} mono />
              <Field k={<GlossaryTerm term="ownerLink">Owner graph ref</GlossaryTerm>} v={b.owner_link_id} mono />
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
              <Field
                k={<GlossaryTerm term="avm">AVM</GlossaryTerm>}
                v=""
                childEl={
                  hasAvm ? (
                    <div className="field__value mono num">{currency(b.avm_value)}</div>
                  ) : (
                    <div>
                      <span className="chip chip--warning chip--compact">AVM unavailable</span>
                      <div className="field__sub">Valuation feed missing for this CLIP; equity math is gated.</div>
                    </div>
                  )
                }
              />
              <Field
                k={<GlossaryTerm term="estimatedUpb">Current lien</GlossaryTerm>}
                v=""
                childEl={
                  <div>
                    {/* `current_rate` is already percent form in gold
                        (`first_pos_rate * 100`, unrounded) — render it through
                        the shared 2-dp rate convention so it can't disagree
                        with the par rate in the refi-economics panel, and so
                        an IEEE754 tail (7.000000000000001) never ships. */}
                    <div className="field__value mono num">{`${currency(b.current_lien_balance)} · ${ratePct(b.current_rate)}`}</div>
                    <div className="field__sub">
                      <div className="chip-row chip-row--baseline">
                        <Chip variant="warning" className="chip--compact" icon="info">
                          Estimated UPB
                        </Chip>
                        <EvidenceChip source={estimatedUpbBandSource}>
                          {lienBandLabel}
                        </EvidenceChip>
                      </div>
                    </div>
                  </div>
                }
              />
              <Field
                k={<><GlossaryTerm term="ltv">LTV</GlossaryTerm> / Equity</>}
                v=""
                childEl={
                  hasAvm ? (
                    <div className="field__value mono num">{`${b.ltv}% · ${currency(b.equity_estimate)}`}</div>
                  ) : (
                    <div>
                      <div className="field__value mono num">—</div>
                      <div className="field__sub">Not a zero-equity signal; AVM was unavailable.</div>
                    </div>
                  )
                }
              />
              <Field k="Related properties" v={`${b.related_property_count} (via owner graph)`} />
              <Field
                k="Metro / loan type"
                v={`${b.situs_cbsa_code ?? 'CBSA unavailable'} · ${b.first_pos_loan_type ?? 'Loan type unavailable'}`}
                mono
              />
              <Field
                k="Relationship flags"
                v=""
                childEl={<BorrowerTruthFlags borrower={b} />}
              />
              <Field
                k="First-party signals"
                v=""
                childEl={
                  firstPartySignalSummary.length > 0 ? (
                    <div className="chip-row">
                      {firstPartySignalSummary.map((label) => (
                        <span key={label} className="chip chip--neutral chip--compact">
                          {label}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="chip chip--neutral chip--compact">No first-party signal</span>
                  )
                }
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
                      const label = safeSegmentName(sid) ?? 'Unknown segment';
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
                          {label}
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
              <div className="h-4">Refi economics check</div>
            </div>
            <div className="surface__body">
              <div className="chip-row mb-3">
                <Chip variant={b.why_panel.in_the_money ? 'success' : 'warning'}>
                  {b.why_panel.in_the_money ? <GlossaryTerm term="inTheMoney">Passes refi screen</GlossaryTerm> : 'Below refi screen'}
                </Chip>
                <span className="mono num text-1">
                  {signedBpsLabel(b.why_panel.rate_spread_bps)}
                </span>
                {/* `why_panel.market_rate` is FRACTION form
                    (`market_rate_fraction`), so it scales once here — same
                    precision as the borrower's own rate above. */}
                <span className="muted fs-12">
                  vs. par {ratePctFromFraction(b.why_panel.market_rate)}
                </span>
              </div>
              <div className="rationale-box">
                <span className="text-1 fw-500">Refinance economics.</span>{' '}
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
                <Button
                  size="sm"
                  icon="audit"
                  onClick={() => setProofOpen(true)}
                  aria-label={`Show proof for borrower ${b.borrower_id}`}
                >
                  Show proof
                </Button>
              </div>
            </div>
          </div>

          <div className="surface">
            <div className="surface__hdr">
              <Icon name="bolt" size={14} className="icon-accent" />
              <div className="h-4"><GlossaryTerm term="nextBestOffer">Primary offer</GlossaryTerm></div>
            </div>
            <div className="surface__body">
              <div className="split-row">
                <div className="offer-title">{offerLabel}</div>
                <ScoreBadge value={b.opportunity_score} />
              </div>
              <p className="body mt-2">{offerDescription}</p>
              <p className="muted fs-12 mt-1">{offerRationale(b.recommended_offer_code, b.why_now)}</p>
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
                <Button
                  variant="ghost"
                  icon="audit"
                  onClick={() => setProofOpen(true)}
                  aria-label={`Show scoring math for borrower ${b.borrower_id}`}
                >
                  Show math
                </Button>
              </div>
            </div>
          </div>

          <div className="surface">
            <div className="surface__hdr">
              <Icon name="layers" size={14} className="icon-accent" />
              <div className="h-4"><GlossaryTerm term="supportingEvidence">Supporting evidence</GlossaryTerm></div>
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
      <BorrowerProofDrawer
        borrowerId={b.borrower_id}
        open={proofOpen}
        onClose={() => setProofOpen(false)}
      />
    </PageShell>
  );
}

function Field({ k, v, mono, childEl }: { k: ReactNode; v: string; mono?: boolean; childEl?: ReactElement }) {
  return (
    <div>
      <div className="field__label">{k}</div>
      {childEl ?? <div className={`field__value ${mono ? 'mono num' : ''}`}>{v}</div>}
    </div>
  );
}
