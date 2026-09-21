/**
 * Synthetic borrower population for the fixture harness.
 *
 * Deterministic (no Math.random, no clock) so every run and every machine
 * renders the same queue. Borrower ids are masked ids in the production shape
 * `B-[0-9A-Z]{13}`; display names are the `Owner XXXXXX` synthetic label the
 * API emits. No real names, addresses or contact fields.
 */
import type { Borrower360, EvidenceEvent, LeadSummary, SegmentCode } from '../../../../src/types';
import { SNAPSHOT_AT, STATES } from './reference';

const ID_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
export const MASKED_BORROWER_ID = /^B-[0-9A-Z]{13}$/;

export function maskedBorrowerId(index: number): string {
  let state = (index + 11) * 2654435761 % 2147483647;
  let id = '';
  for (let position = 0; position < 13; position += 1) {
    state = (state * 48271 + position * 7919 + 12345) % 2147483647;
    id += ID_ALPHABET[state % ID_ALPHABET.length];
  }
  return `B-${id}`;
}

interface OfferProfile {
  code: string;
  label: string;
  segments: SegmentCode[];
  whyNow: string;
}

const OFFER_PROFILES: readonly OfferProfile[] = [
  { code: 'refi_plus_heloc', label: 'Refinance + HELOC', segments: ['itm', 'equity'], whyNow: 'Lien rate is well above par and estimated equity clears the home-equity threshold.' },
  { code: 'heloc', label: 'HELOC', segments: ['permit', 'equity'], whyNow: 'Cotality HELOC propensity and a strong equity position indicate equity-credit demand.' },
  { code: 'purchase', label: 'Next-home purchase loan', segments: ['listed', 'retention'], whyNow: 'An active listing makes next-home financing more useful than refinancing the current loan.' },
  { code: 'cash_out', label: 'Cash-out refinance', segments: ['itm', 'investor'], whyNow: 'Owner Link shows a multi-property profile with a lien currently in the money.' },
  { code: 'refi', label: 'Refinance', segments: ['itm'], whyNow: 'Rate spread clears the refinance economics screen on the latest AVM.' },
  { code: 'retention', label: 'Retention review', segments: ['retention'], whyNow: 'Current-customer relationship with competitor lien activity nearby.' },
];

function evidenceFor(index: number, spreadBps: number, equity: number): EvidenceEvent[] {
  const suffix = String(index + 1).padStart(3, '0');
  return [
    { evidence_id: `ev-${suffix}-a`, source_product: 'Voluntary Lien', source_table: 'cotality.liens.voluntary_lien', signal_type: 'rate_spread', signal_value: `+${spreadBps} bps`, display_text: `Current lien rate is ${spreadBps} bps above par.`, confidence: 0.92, timestamp: '2026-07-12T06:12:00Z' },
    { evidence_id: `ev-${suffix}-b`, source_product: 'AVM', source_table: 'cotality.avm.current', signal_type: 'equity', signal_value: `$${Math.round(equity / 1000)}K`, display_text: 'Estimated equity is above the home-equity threshold.', confidence: 0.88, timestamp: '2026-07-10T06:12:00Z' },
    { evidence_id: `ev-${suffix}-c`, source_product: 'Mortgage Market Analytics', source_table: 'cotality.mma.refi_activity', signal_type: 'market_trend', signal_value: '+28% QoQ', display_text: 'Local refinance activity is up 28% quarter over quarter.', confidence: 0.84, timestamp: '2026-07-02T06:12:00Z' },
  ];
}

interface Economics {
  tag: string;
  spreadBps: number;
  avmValue: number;
  equity: number;
  lienBalance: number;
  evidence: EvidenceEvent[];
}

function economicsFor(index: number): Economics {
  const spreadBps = 84 + ((index * 29) % 13) * 12;
  const avmValue = 480000 + ((index * 37) % 11) * 45000;
  const equity = 140000 + ((index * 37) % 11) * 31000;
  return {
    tag: maskedBorrowerId(index).slice(2, 8),
    spreadBps,
    avmValue,
    equity,
    lienBalance: avmValue - equity,
    evidence: evidenceFor(index, spreadBps, equity),
  };
}

/** The columns /api/leads returns for one ranked borrower. */
function buildLead(index: number): LeadSummary {
  const state = STATES[index % STATES.length];
  const profile = OFFER_PROFILES[index % OFFER_PROFILES.length];
  const { tag, spreadBps, equity, lienBalance, evidence } = economicsFor(index);
  const approval: LeadSummary['approval_status'] = index === 1 ? 'approved' : index === 9 ? 'rejected' : 'pending';
  const isInvestor = profile.segments.includes('investor');
  return {
    borrower_id: maskedBorrowerId(index),
    display_name: `Owner ${tag}`,
    city: state.city,
    state: state.code,
    zip: state.zip,
    clip: `clip_demo_${tag.toLowerCase()}`,
    segment_codes: profile.segments,
    equity_estimate: equity,
    rate_spread_bps: spreadBps,
    opportunity_score: Math.max(52, 96 - index * 2),
    confidence: Math.max(48, 92 - ((index * 7) % 40)),
    recommended_offer_code: profile.code,
    recommended_offer: profile.label,
    why_now: profile.whyNow,
    evidence_ids: evidence.map((event) => event.evidence_id),
    approval_status: approval,
    outreach_status: approval === 'approved' ? 'queued' : 'none',
    is_owner_occupied: !isInvestor,
    is_investor: isInvestor,
    is_current_customer: profile.segments.includes('retention'),
    related_property_count: isInvestor ? 3 : 1,
    current_lien_balance: lienBalance,
    listed_for_sale: profile.segments.includes('listed'),
    has_heloc_propensity_trigger: profile.segments.includes('permit'),
    marketing_eligible: true,
    consent_status: 'opt_in',
    suppression_reason: null,
    owner_count: 1,
    has_unresolved_owner: false,
    primary_owner_entity_type: 'individual',
    eligibility_source: 'synthetic_seed',
    loan_product_type: index % 3 === 0 ? 'conventional' : index % 3 === 1 ? 'fha' : 'jumbo',
    origination_channel: index % 2 === 0 ? 'loan_officer' : 'digital',
  };
}

/** The full dossier GET /api/borrowers/:id returns for the same borrower. */
function buildBorrower(index: number): Borrower360 {
  const lead = buildLead(index);
  const { tag, spreadBps, avmValue, lienBalance, evidence } = economicsFor(index);
  const ltv = Math.round((lienBalance / avmValue) * 100);
  const equityPct = 100 - ltv;
  return {
    ...lead,
    source_refreshed_at: SNAPSHOT_AT,
    clip_id: lead.clip,
    owner_link_id: `ol_demo_${tag.toLowerCase()}`,
    subject_property: `Synthetic property · ${lead.city}, ${lead.state} ${lead.zip}`,
    avm_value: avmValue,
    current_lien_balance: lienBalance,
    current_rate: Math.round((4.875 + spreadBps / 100) * 1000) / 1000,
    ltv,
    related_property_count: lead.related_property_count ?? 1,
    trigger_timeline: evidence,
    evidence_events: evidence,
    why_panel: {
      rate_spread_bps: spreadBps,
      market_rate: 0.04875,
      equity_pct: equityPct,
      in_the_money: spreadBps >= 75 && equityPct >= 15,
      in_the_money_reason: `+${spreadBps} bps spread (>= 75) AND ${equityPct}% equity (>= 15%)`,
      min_spread_bps: 75,
      min_equity_pct: 15,
      sources: ['mip.gold.fn_rate_spread', 'mip.gold.fn_in_the_money'],
      source_labels: [
        { name: 'mip.gold.fn_rate_spread', display_label: 'Rate spread rule' },
        { name: 'mip.gold.fn_in_the_money', display_label: 'In-the-money rule' },
      ],
    },
  };
}

export const BORROWER_COUNT = 24;

const INDEXES = Array.from({ length: BORROWER_COUNT }, (_, index) => index);

/** Ranked by opportunity score, highest first (the order /api/leads returns). */
export const LEADS: readonly LeadSummary[] = INDEXES.map(buildLead);
export const BORROWERS: readonly Borrower360[] = INDEXES.map(buildBorrower);

/** The borrower the detail routes in the smoke spec open. */
export const PRIMARY_BORROWER: Borrower360 = BORROWERS[0];

export function borrowerById(borrowerId: string): Borrower360 | undefined {
  return BORROWERS.find((borrower) => borrower.borrower_id === borrowerId);
}
