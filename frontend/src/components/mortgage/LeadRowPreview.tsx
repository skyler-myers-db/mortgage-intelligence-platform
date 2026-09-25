import type { CSSProperties } from 'react';
import { Link } from 'react-router';
import type { LeadSummary } from '../../types';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { compactCurrency, signedBpsLabel } from '../../lib/formatters';
import { offerDisplayLabel, offerRationale, offerShortDescription } from '../../lib/offerLanguage';
import { safeSegmentName, segmentColor } from '../../lib/segmentMetadata';
import { genieLeadPrompt } from '../../lib/genieContext';
import { useQueueLinkState } from '../../lib/queueContext';
import { useApp } from '../AppContext';
import { GenieAskAbout } from './GenieAskAbout';
import { Button, EvidenceChip } from '../Primitives';
import { ConfidenceMeter } from './ConfidenceMeter';
import type { LeadDecisionReceipt } from './DecisionReceipt';
import { ScoreAnatomyGate } from './ScoreAnatomyGate';
import { ScoreBadge } from './ScoreBadge';
import { lazyModule, useLazyModule } from './useLazyModule';
import { dispositionLabel, outreachLabel } from './LeadTable.logic';

// A row's Decision receipt renders only after a decision in this session, so
// it loads then (its shared interaction chunk) instead of riding in the Lead
// Queue's route closure (budget, wave 3). The row's status chip states the
// outcome meanwhile, and the receipt reads nothing before it mounts.
const RECEIPT_CHUNK = lazyModule(() => import('./DecisionReceipt'));

/**
 * id of the expanded row's receipt block: the queue's "View receipt" toast
 * action opens the row and moves focus here (tabIndex -1).
 */
export function leadReceiptAnchorId(borrowerId: string): string {
  return `lead-receipt-${borrowerId}`;
}

/**
 * @param approval Effective approval state — the in-session optimistic
 *   override merged with the server projection (the same value the row's
 *   status chip renders). Re-audit #3 (2026-06-12): the preview read only
 *   `lead.approval_status`, so right after an approve the chip said
 *   "Approved" while this panel still said "pending" — which then made
 *   the (correctly no-op'ing) A/R hotkeys look broken on a terminal row.
 * @param decisionReceipt The audit row a row approve / reject in this
 *   session wrote. The expanded row reads it back as a Decision receipt
 *   (wow-stage-3); nothing about the receipt is taken from the POST body
 *   except the outcome it resolved with, which stays visible when the
 *   read-back is refused or fails.
 *   The reveal plays once per decision: after it has played, a collapse +
 *   re-expand renders the receipt finished (motion-06).
 */
export function RowPreview({
  lead,
  approval,
  decisionReceipt = null,
}: {
  lead: LeadSummary;
  approval?: string;
  decisionReceipt?: LeadDecisionReceipt | null;
}) {
  const { setLastBorrowerId, saveLead, isLeadSaved } = useApp();
  const DecisionReceipt = useLazyModule(RECEIPT_CHUNK, Boolean(decisionReceipt?.auditEventId)).module?.DecisionReceipt;
  const queueLinkState = useQueueLinkState(); // shell-04: dossier crumbs + pager
  // Prefer the display-safe Cotality property ref projected by the
  // backend. Raw CLIP is masked server-side for public demo safety.
  const propertyRef = lead.clip && lead.clip.length > 0
    ? lead.clip
    : 'Property ref unavailable';
  const saved = isLeadSaved(lead.borrower_id);
  const askPrompt = genieLeadPrompt({ segmentCodes: lead.segment_codes, stateCode: lead.state });
  const saveCurrentLead = () => {
    saveLead({
      borrower_id: lead.borrower_id,
      city: lead.city,
      state: lead.state,
      zip: lead.zip,
      recommended_offer: lead.recommended_offer,
      opportunity_score: lead.opportunity_score,
      confidence: lead.confidence,
    });
  };
  return (
    <>
      {decisionReceipt?.auditEventId && (
        <div
          id={leadReceiptAnchorId(lead.borrower_id)}
          className="tbl__expand-inner tbl__expand-inner--receipt"
          tabIndex={-1}
        >
          {DecisionReceipt && (
            <DecisionReceipt
              auditEventId={decisionReceipt.auditEventId}
              decision={decisionReceipt.decision}
              decidedHere
              reveal={!decisionReceipt.revealed}
              onRevealed={decisionReceipt.markRevealed}
              compact
              headingLevel={3}
              score={{ opportunityScore: lead.opportunity_score, confidence: lead.confidence }}
            />
          )}
        </div>
      )}
    <div className="tbl__expand-inner tbl__expand-inner--lead">
      <div>
        <div className="eyebrow mb-2">Borrower 360 preview</div>
        <div className="preview-grid">
          <Cell k="Property ref"  v={propertyRef} mono />
          <Cell k="Location"      v={`${lead.city}, ${lead.state} · ${lead.zip}`} />
          <Cell k="Equity"        v={compactCurrency(lead.equity_estimate)} mono />
          <Cell k="Rate spread"   v={signedBpsLabel(lead.rate_spread_bps)} mono />
          <Cell k="Score"         v={`${lead.opportunity_score}`} mono />
          <Cell k="Signal"        v={`${lead.confidence}%`} mono />
          <Cell k="Approval"      v={approval ?? lead.approval_status ?? 'pending'} />
          <Cell k="Outreach"      v={outreachLabel(lead.outreach_status)} />
          <Cell k="Assigned to"   v={lead.assigned_to_label ?? lead.assigned_to_email ?? 'Unassigned'} />
          <Cell k="Last touch"    v={dispositionLabel(lead.latest_disposition_outcome)} />
        </div>
        <div className="eyebrow mt-4 mb-2">Segments</div>
        <div className="chip-row">
          {lead.segment_codes.map((sid) => {
            const color = segmentColor(sid);
            const label = safeSegmentName(sid) ?? 'Unknown segment';
            return (
              <span
                key={sid}
                className="chip chip--segment"
                style={{ '--chip-hue': color } as CSSProperties}
              >
                <span className="chip__label">{label}</span>
              </span>
            );
          })}
        </div>
        {/* "Ask Genie about this borrower's segment and state" (genie-04):
            the prompt names the registered segment and the federal state
            only -- never the masked id, the property ref or a contact field. */}
        {askPrompt && (
          <div className="chip-row mt-2">
            <GenieAskAbout prompt={askPrompt} subject="borrower's segment and state" />
          </div>
        )}
      </div>

      <div>
        <div className="eyebrow mb-2">Why now</div>
        <p className="body flush">{offerRationale(lead.recommended_offer_code, lead.why_now)}</p>
        <div className="chip-row mt-3">
          <span className="muted fs-11">Decision inputs:</span>
          <EvidenceChip source={DRAWER_SOURCES.itm}>{DRAWER_SOURCES.itm.title}</EvidenceChip>
          <EvidenceChip source={DRAWER_SOURCES.leadScore}>{DRAWER_SOURCES.leadScore.title}</EvidenceChip>
          <EvidenceChip source={DRAWER_SOURCES.nbo}>{DRAWER_SOURCES.nbo.title}</EvidenceChip>
          <EvidenceChip source={DRAWER_SOURCES.ownerGraph}>{DRAWER_SOURCES.ownerGraph.title}</EvidenceChip>
          {lead.equity_estimate > 0 && (
            <EvidenceChip source={DRAWER_SOURCES.avm}>{DRAWER_SOURCES.avm.title}</EvidenceChip>
          )}
          {(lead.current_lien_balance ?? 0) > 0 && (
            <EvidenceChip source={DRAWER_SOURCES.lien}>{DRAWER_SOURCES.lien.title}</EvidenceChip>
          )}
          {lead.has_permit === true && (
            <EvidenceChip source={DRAWER_SOURCES.permit}>{DRAWER_SOURCES.permit.title}</EvidenceChip>
          )}
          {lead.has_heloc_propensity_trigger === true && (
            <EvidenceChip source={DRAWER_SOURCES.helocPropensity}>{DRAWER_SOURCES.helocPropensity.title}</EvidenceChip>
          )}
          {lead.has_refi_propensity_trigger === true && (
            <EvidenceChip source={DRAWER_SOURCES.refiPropensity}>{DRAWER_SOURCES.refiPropensity.title}</EvidenceChip>
          )}
          {lead.listed_for_sale === true && (
            <EvidenceChip source={DRAWER_SOURCES.mls}>{DRAWER_SOURCES.mls.title}</EvidenceChip>
          )}
        </div>
      </div>

      <div>
        <div className="eyebrow mb-2">Primary offer</div>
        <div className="surface preview-offer-card">
          <div className="split-row">
            <div className="offer-title">
              {offerDisplayLabel(lead.recommended_offer_code, lead.recommended_offer)}
            </div>
            <ScoreBadge value={lead.opportunity_score} />
          </div>
          {/* wow-stage-2: a disclosure, never read on expand. The chunk mounts
              its own proof drawer, opened over the cache. */}
          <ScoreAnatomyGate borrowerId={lead.borrower_id} variant="spine" />
          <p className="muted fs-12 mt-1 flush">
            {offerShortDescription(lead.recommended_offer_code)}
          </p>
          <div className="muted fs-12 mt-1">
            Signal <ConfidenceMeter value={lead.confidence} compact />
          </div>
          <div className="chip-row mt-3">
            <Link
              className="btn btn--primary btn--sm"
              to={`/borrower-360/${lead.borrower_id}`}
              state={queueLinkState}
              onClick={() => setLastBorrowerId(lead.borrower_id)}
            >
              Open Borrower 360
            </Link>
            <Link
              className="btn btn--default btn--sm"
              to={`/offer-orchestrator/${lead.borrower_id}`}
              state={queueLinkState}
              onClick={() => setLastBorrowerId(lead.borrower_id)}
            >
              Build offer
            </Link>
            <Button
              variant={saved ? 'ghost' : 'default'}
              size="sm"
              icon={saved ? 'check' : 'tag'}
              onClick={saveCurrentLead}
              aria-label={`${saved ? 'Saved' : 'Save'} borrower ${lead.borrower_id}`}
            >
              {saved ? 'Saved' : 'Save lead'}
            </Button>
          </div>
        </div>
      </div>
    </div>
    </>
  );
}

function Cell({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div>
      <div className="field__label">{k}</div>
      <div className={`field__value ${mono ? 'mono num' : ''}`}>{v}</div>
    </div>
  );
}
