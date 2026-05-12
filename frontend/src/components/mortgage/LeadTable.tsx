import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import type { CallDisposition, LeadSummary, SalesTeamMember } from '../../types';
import { Icon } from '../Icon';
import { Chip, Button, EvidenceChip } from '../Primitives';
import { ScoreBadge } from './ScoreBadge';
import { ConfidenceMeter } from './ConfidenceMeter';
import { useApp } from '../AppContext';
import { api, ApiError, isAbortError } from '../../lib/api';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { segmentColor, segmentName } from '../../lib/segmentMetadata';

/**
 * LeadTable — prototype `.surface` + `.tbl` BEM. Sticky thead, hover, row
 * expand into a mini borrower-detail preview. Approvals track per-row via
 * AppContext; a chip on the rightmost column shows Pending / Approved /
 * Rejected.
 *
 * LO friction fix (2026-04-22): the Approval column is now an inline
 * control, not a read-only chip. Pending rows expose an "Approve" primary
 * button + a reject icon so loan officers can burn through the queue in
 * one click per lead instead of navigating to Offer Orchestrator. Once
 * approved/rejected, the column reverts to the chip shape. Keyboard
 * shortcuts (A / R) act on the expanded row.
 *
 * Sales-ops bulk workflow (2026-04-22): a leftmost checkbox column selects
 * rows for bulk approval. When >= 1 row is selected, a sticky action bar
 * inside the table container offers "Approve N leads" / "Clear selection".
 * Bulk approve loops `api.approve()` per selected lead in chunks of 3 to
 * keep one audit row per approval (matching the single-row flow). Already
 * approved/rejected rows are skipped silently. Shift+A fires bulk-approve
 * when the table has focus; plain A still approves only the expanded row.
 */

/** Concurrency cap for the bulk-approve client-side loop. */
const BULK_APPROVE_CONCURRENCY = 3;

export interface LeadExportContext {
  generatedAt?: string;
  filters?: string;
  refreshedAt?: string | null;
  rulesVersion?: string | null;
}

interface LeadTableProps {
  leads: LeadSummary[];
  totalMatching?: number | null;
  truncatedAt?: number | null;
  exportContext?: LeadExportContext;
  salesTeam?: SalesTeamMember[];
}

type RejectReasonCode =
  | 'out_of_footprint'
  | 'do_not_call'
  | 'opt_out'
  | 'fair_lending_review'
  | 'low_intent'
  | 'data_quality'
  | 'other_with_text';

const REJECT_REASONS: { code: RejectReasonCode; label: string }[] = [
  { code: 'low_intent', label: 'Low intent' },
  { code: 'do_not_call', label: 'Do Not Call' },
  { code: 'opt_out', label: 'Opt-out' },
  { code: 'fair_lending_review', label: 'Fair-lending review' },
  { code: 'data_quality', label: 'Data quality' },
  { code: 'out_of_footprint', label: 'Out of footprint' },
  { code: 'other_with_text', label: 'Other' },
];

const DISPOSITION_OPTIONS: { outcome: CallDisposition['outcome']; label: string }[] = [
  { outcome: 'called_no_answer', label: 'No answer' },
  { outcome: 'called_left_voicemail', label: 'Left voicemail' },
  { outcome: 'connected', label: 'Connected' },
  { outcome: 'callback_scheduled', label: 'Callback scheduled' },
  { outcome: 'application_started', label: 'Application started' },
  { outcome: 'not_interested', label: 'Not interested' },
  { outcome: 'not_now', label: 'Not now' },
  { outcome: 'dead', label: 'Dead lead' },
];

type SortKey = 'rank' | 'relationship' | 'assignment' | 'outreach' | 'equity' | 'rate' | 'score' | 'confidence';
type SortDir = 'asc' | 'desc';

/**
 * Return true when `el` is an editable element that the window-level
 * hotkey handler must skip over. Exported for unit tests — the
 * actual hotkey listener uses both this and `document.activeElement`
 * so typing "a" into a text field can never trigger bulk-approve.
 * R5-12 (2026-04-23).
 */
export function isEditableTarget(el: Element | null | undefined): boolean {
  if (!el) return false;
  const tag = (el as HTMLElement).tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return (el as HTMLElement).isContentEditable === true;
}

/**
 * Chunk a list into groups of `size`. Used by the bulk-approve loop to
 * bound in-flight POSTs to the approve endpoint without inventing a
 * server-side bulk API.
 */
function chunk<T>(items: T[], size: number): T[][] {
  if (size <= 0) return [items];
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    out.push(items.slice(i, i + size));
  }
  return out;
}

function _newBulkId(): string {
  const c = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const n = ch === 'x' ? Math.floor(Math.random() * 16) : 8 + Math.floor(Math.random() * 4);
    return n.toString(16);
  });
}

function csvEscape(raw: string): string {
  const v = /^[=+\-@]/.test(raw) ? `'${raw}` : raw;
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

function csvValue(raw: unknown): string {
  if (raw === null || raw === undefined) return '';
  if (typeof raw === 'boolean') return raw ? 'true' : 'false';
  if (typeof raw === 'number') return Number.isFinite(raw) ? String(raw) : '';
  return String(raw);
}

function relationshipLabel(lead: LeadSummary): string {
  if (lead.is_current_customer) return 'Current';
  if (lead.is_former_customer) return 'Former';
  if (lead.is_competitor_lien) return 'Competitor';
  return 'Other';
}

function relationshipVariant(lead: LeadSummary): 'success' | 'warning' | 'neutral' {
  if (lead.is_current_customer) return 'success';
  if (lead.is_competitor_lien) return 'warning';
  return 'neutral';
}

function sortValue(lead: LeadSummary, key: SortKey): string | number {
  if (key === 'relationship') return relationshipLabel(lead);
  if (key === 'assignment') return lead.assigned_to_label ?? lead.assigned_to_email ?? 'Unassigned';
  if (key === 'outreach') return lead.outreach_status ?? 'none';
  if (key === 'equity') return lead.equity_estimate;
  if (key === 'rate') return lead.rate_spread_bps;
  if (key === 'score') return lead.opportunity_score;
  if (key === 'confidence') return lead.confidence;
  return 0;
}

function outreachLabel(status?: string | null): string {
  if (!status || status === 'none') return 'None';
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function outreachVariant(status?: string | null): 'success' | 'warning' | 'neutral' {
  if (status === 'sent' || status === 'replied' || status === 'actioned') return 'success';
  if (status === 'queued' || status === 'bounced') return 'warning';
  return 'neutral';
}

function isTerminalApproval(status?: string | null): boolean {
  return status === 'approved' || status === 'rejected' || status === 'hold';
}

function isNonWorkableApproval(status?: string | null): boolean {
  return status === 'rejected' || status === 'hold';
}

export function isLeadSelectableForSalesOps(status?: string | null, localStatus?: string | null): boolean {
  return !isNonWorkableApproval(status) && !isNonWorkableApproval(localStatus);
}

export function isLeadApprovalEligible(status?: string | null, localStatus?: string | null): boolean {
  return !localStatus && !isTerminalApproval(status);
}

function dispositionLabel(outcome?: string | null): string {
  if (!outcome) return 'Untouched';
  return outcome.split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function dispositionVariant(outcome?: string | null): 'success' | 'warning' | 'neutral' | 'danger' {
  if (outcome === 'application_started' || outcome === 'callback_scheduled' || outcome === 'connected') return 'success';
  if (outcome === 'called_no_answer' || outcome === 'called_left_voicemail' || outcome === 'not_now') return 'warning';
  if (outcome === 'dead' || outcome === 'not_interested') return 'danger';
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

export function buildLeadCsv(
  leads: LeadSummary[],
  approvals: Record<string, string | undefined> = {},
  context: LeadExportContext = {},
): string {
  const header = [
    'borrower_id',
    'property_ref',
    'city',
    'state',
    'zip',
    'segments',
    'equity_estimate',
    'rate_spread_bps',
    'opportunity_score',
    'confidence',
    'recommended_offer',
    'approval_status',
    'outreach_status',
    'approved_at',
    'outreach_at',
    'assigned_to_email',
    'assigned_at',
    'latest_disposition_outcome',
    'latest_disposition_at',
    'latest_callback_at',
    'aging_days',
    'is_owner_occupied',
    'is_investor',
    'is_current_customer',
    'is_former_customer',
    'is_competitor_lien',
    'current_lender_ref',
    'current_lien_balance',
    'second_pos_amount',
    'has_permit',
    'listed_for_sale',
    'related_property_count',
    'marketing_eligible',
    'consent_status',
    'suppression_reason',
    'last_touch_at',
    'eligible_recontact_at',
  ];
  const exportableLeads = leads.filter((lead) => lead.marketing_eligible === true);
  const metadata = [
    ['generated_at', context.generatedAt ?? new Date().toISOString()],
    ['filters', context.filters ?? 'none'],
    ['suppression_policy', 'eligible_only_default; non-eligible visible rows are excluded from client CSV'],
    ['refreshed_at', context.refreshedAt ?? 'unknown'],
    ['rules_version', context.rulesVersion ?? 'unknown'],
  ].map(([key, value]) => `# ${key}=${String(value).replace(/\r?\n/g, ' ')}`);
  const rows = exportableLeads.map((l) =>
    [
      l.borrower_id,
      l.clip ?? '',
      l.city,
      l.state,
      l.zip,
      l.segment_codes.join('|'),
      l.equity_estimate,
      l.rate_spread_bps,
      l.opportunity_score,
      l.confidence,
      l.recommended_offer,
      approvals[l.borrower_id] ?? l.approval_status ?? 'pending',
      l.outreach_status ?? 'none',
      l.approved_at ?? '',
      l.outreach_at ?? '',
      l.assigned_to_email ?? '',
      l.assigned_at ?? '',
      l.latest_disposition_outcome ?? '',
      l.latest_disposition_at ?? '',
      l.latest_callback_at ?? '',
      l.aging_days ?? '',
      l.is_owner_occupied,
      l.is_investor,
      l.is_current_customer,
      l.is_former_customer,
      l.is_competitor_lien,
      l.current_lender_ref,
      l.current_lien_balance,
      l.second_pos_amount,
      l.has_permit,
      l.listed_for_sale,
      l.related_property_count,
      l.marketing_eligible,
      l.consent_status ?? 'unknown',
      l.suppression_reason ?? '',
      l.last_touch_at ?? '',
      l.eligible_recontact_at ?? '',
    ]
      .map((value) => csvEscape(csvValue(value)))
      .join(','),
  );
  return [...metadata, header.join(','), ...rows].join('\n');
}

function RowPreview({ lead }: { lead: LeadSummary }) {
  const { setLastBorrowerId, saveLead, isLeadSaved } = useApp();
  // Prefer the display-safe Cotality property ref projected by the
  // backend. Raw CLIP is masked server-side for public demo safety.
  const propertyRef = lead.clip && lead.clip.length > 0
    ? lead.clip
    : 'property_ref_unavailable';
  const saved = isLeadSaved(lead.borrower_id);
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
    <div className="tbl__expand-inner tbl__expand-inner--lead">
      <div>
        <div className="eyebrow mb-2">Customer 360 preview</div>
        <div className="preview-grid">
          <Cell k="Property ref"  v={propertyRef} mono />
          <Cell k="Location"      v={`${lead.city}, ${lead.state} · ${lead.zip}`} />
          <Cell k="Equity"        v={`$${(lead.equity_estimate / 1000).toFixed(0)}k`} mono />
          <Cell k="Rate spread"   v={`+${lead.rate_spread_bps} bps`} mono />
          <Cell k="Score"         v={`${lead.opportunity_score}`} mono />
          <Cell k="Confidence"    v={`${lead.confidence}%`} mono />
          <Cell k="Approval"      v={lead.approval_status ?? 'pending'} />
          <Cell k="Outreach"      v={outreachLabel(lead.outreach_status)} />
          <Cell k="Assigned to"   v={lead.assigned_to_label ?? lead.assigned_to_email ?? 'Unassigned'} />
          <Cell k="Last touch"    v={dispositionLabel(lead.latest_disposition_outcome)} />
        </div>
        <div className="eyebrow mt-4 mb-2">Segments</div>
        <div className="chip-row">
          {lead.segment_codes.map((sid) => {
            const color = segmentColor(sid);
            return (
              <span
                key={sid}
                className="chip chip--segment"
                style={{ '--chip-hue': color } as CSSProperties}
              >
                {segmentName(sid)}
              </span>
            );
          })}
        </div>
      </div>

      <div>
        <div className="eyebrow mb-2">Why now</div>
        <p className="body flush">{lead.why_now}</p>
        <div className="chip-row mt-3">
          <span className="muted fs-11">Decision inputs:</span>
          {/*
            Prototype-parity-audit P1-5 (2026-05-04): the row preview
            previously surfaced only two chips — Rate + equity ruleset and
            Next-best-offer model — which understated the depth of evidence
            the platform actually carries. Borrower 360 already renders 5+
            chips per dossier; the inline lead-queue preview should match
            that posture so an LO scrolling the queue can see what each
            recommendation is grounded in without opening the dossier. We
            Render a fixed core set and append data-driven chips for whichever
            signals the row carries. Each chip routes to a distinct drawer
            entry; related evidence can share upstream Cotality assets, but the
            drawer should still describe the specific primitive being cited.
          */}
          <EvidenceChip source={DRAWER_SOURCES.itm}>Rate + equity ruleset</EvidenceChip>
          <EvidenceChip source={DRAWER_SOURCES.leadScore}>Lead score model</EvidenceChip>
          <EvidenceChip source={DRAWER_SOURCES.nbo}>Next-best-offer model</EvidenceChip>
          <EvidenceChip source={DRAWER_SOURCES.ownerGraph}>Property + owner graph</EvidenceChip>
          {lead.equity_estimate > 0 && (
            <EvidenceChip source={DRAWER_SOURCES.avm}>AVM equity</EvidenceChip>
          )}
          {(lead.current_lien_balance ?? 0) > 0 && (
            <EvidenceChip source={DRAWER_SOURCES.lien}>Voluntary lien</EvidenceChip>
          )}
          {lead.has_permit === true && (
            <EvidenceChip source={DRAWER_SOURCES.permit}>Recent permit</EvidenceChip>
          )}
          {lead.listed_for_sale === true && (
            <EvidenceChip source={DRAWER_SOURCES.mls}>MLS listing</EvidenceChip>
          )}
        </div>
      </div>

      <div>
        <div className="eyebrow mb-2">Next-best-offer</div>
        <div className="surface preview-offer-card">
          <div className="split-row">
            <div className="offer-title">{lead.recommended_offer}</div>
            <ScoreBadge value={lead.opportunity_score} />
          </div>
          <div className="muted fs-12 mt-1">
            Confidence <ConfidenceMeter value={lead.confidence} compact />
          </div>
          <div className="chip-row mt-3">
            <Link
              className="btn btn--primary btn--sm"
              to={`/borrower-360/${lead.borrower_id}`}
              onClick={() => setLastBorrowerId(lead.borrower_id)}
            >
              Open Borrower 360
            </Link>
            <Link
              className="btn btn--default btn--sm"
              to={`/offer-orchestrator/${lead.borrower_id}`}
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

export function LeadTable({ leads, totalMatching = null, truncatedAt = null, exportContext, salesTeam = [] }: LeadTableProps) {
  const [expanded, setExpanded] = useState<string | null>(leads[0]?.borrower_id ?? null);
  const [sortKey, setSortKey] = useState<SortKey>('rank');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [pendingReject, setPendingReject] = useState<string | null>(null);
  const [rejectReasonCode, setRejectReasonCode] = useState<RejectReasonCode>('low_intent');
  const [rejectRationale, setRejectRationale] = useState('');
  const [bulkRationaleOpen, setBulkRationaleOpen] = useState(false);
  const [bulkRationale, setBulkRationale] = useState('');
  const [selectedAssignee, setSelectedAssignee] = useState<string>('');
  const [salesToast, setSalesToast] = useState<string | null>(null);
  const [salesBusy, setSalesBusy] = useState(false);
  const [salesOverrides, setSalesOverrides] = useState<Record<string, Partial<LeadSummary>>>({});
  const [pendingDisposition, setPendingDisposition] = useState<string | null>(null);
  const [dispositionOutcome, setDispositionOutcome] = useState<CallDisposition['outcome']>('called_left_voicemail');
  const [dispositionLo, setDispositionLo] = useState<string>('');
  const [dispositionCallbackAt, setDispositionCallbackAt] = useState('');
  const [dispositionNotes, setDispositionNotes] = useState('');
  const { approvals, setApproval, setLastBorrowerId } = useApp();
  const leadsById = useMemo(
    () => new Map(leads.map((lead) => [lead.borrower_id, { ...lead, ...(salesOverrides[lead.borrower_id] ?? {}) }])),
    [leads, salesOverrides],
  );
  const displayLeads = useMemo(
    () => leads.map((lead) => ({ ...lead, ...(salesOverrides[lead.borrower_id] ?? {}) })),
    [leads, salesOverrides],
  );
  const sortedLeads = useMemo(() => {
    if (sortKey === 'rank') return displayLeads;
    const direction = sortDir === 'asc' ? 1 : -1;
    return [...displayLeads].sort((a, b) => {
      const av = sortValue(a, sortKey);
      const bv = sortValue(b, sortKey);
      if (typeof av === 'number' && typeof bv === 'number') {
        return (av - bv) * direction;
      }
      return String(av).localeCompare(String(bv)) * direction;
    });
  }, [displayLeads, sortDir, sortKey]);
  const [pendingApproval, setPendingApproval] = useState<Record<string, boolean>>({});
  const [approvalError, setApprovalError] = useState<string | null>(null);
  // Bulk-approve state. `selectedIds` is a Set so toggling is O(1); we
  // copy-on-write when updating to keep React's reference check happy.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [bulkApproving, setBulkApproving] = useState<boolean>(false);
  // R5-04 (2026-04-23): synchronous in-flight latches. `setState` is
  // async so two rapid clicks can both read `bulkApproving=false` before
  // either commit schedules, producing two parallel approve loops that
  // each write an audit row per borrower. `useRef` gives us a
  // synchronous read/write we can flip before returning from the click
  // handler; the existing React state still drives the disabled UI.
  const bulkInFlightRef = useRef<boolean>(false);
  const rowInFlightRef = useRef<Record<string, boolean>>({});
  // Tracks the bulk-approve loop's AbortController so unmount can
  // cancel the remaining in-flight POSTs. Round-2 hole-finder #10/#11,
  // 2026-04-23.
  const bulkAbortRef = useRef<AbortController | null>(null);
  // Last bulk result surfaced as a compact toast. Clears on the next bulk
  // run or when the user dismisses it (auto-dismiss after 4s).
  //
  // `network` is the subset of `fail` that failed with an unreachable
  // backend (ApiError.status === null) — these rows never reached the
  // audit table and the approver should retry them explicitly.
  // Hole-finder finding #2, 2026-04-23.
  //
  // `aborted` rows fall in an ambiguous state: the client cancelled the
  // POST mid-flight on unmount, but the server may have already
  // committed the audit row. We surface these as a distinct "check the
  // audit log" message rather than pushing retry language, because a
  // blind retry can produce a duplicate audit row. R5-21 (2026-04-23).
  //
  // TODO: once R5-01 (server-side idempotency keys on /api/outreach/
  // approve) lands, aborted becomes safe to retry and this branch can
  // collapse back into the network-retry path.
  const [bulkToast, setBulkToast] = useState<
    { ok: number; fail: number; network: number; aborted: number } | null
  >(null);

  useEffect(() => {
    if (expanded) setLastBorrowerId(expanded);
  }, [expanded, setLastBorrowerId]);

  /**
   * Approve from the queue without leaving the page. Uses the same
   * `/api/outreach/approve` endpoint Offer Orchestrator calls. We mark
   * the row as 'approved' in AppContext optimistically on success so the
   * chip flips immediately and stays flipped on route change.
   *
   * Returns a tagged outcome so bulk-approve can distinguish a network
   * drop ("request never reached the audit table") from a backend
   * rejection ("server said no"). Hole-finder finding #2, 2026-04-23.
   */
  const approveLead = useCallback(
    async (
      borrowerId: string,
      signal?: AbortSignal,
      extras: { rationale?: string | null; bulk_id?: string | null; bulk_rationale?: string | null } = {},
    ): Promise<'ok' | 'network' | 'backend' | 'aborted' | 'duplicate'> => {
      // R5-04: synchronous latch check. setState is async, so a rapid
      // second click could slip in before `pendingApproval[id]` flips
      // to true and produce a second audit row. The ref flips
      // immediately.
      if (rowInFlightRef.current[borrowerId]) return 'duplicate';
      rowInFlightRef.current[borrowerId] = true;
      setApprovalError(null);
      setPendingApproval((p) => ({ ...p, [borrowerId]: true }));
      try {
        const lead = leadsById.get(borrowerId);
        const draft = await api.draftOutreach(borrowerId, 'email', signal);
        const res = await api.approve(
          borrowerId,
          {
            evidence_ids: lead?.evidence_ids ?? [],
            offer_code: draft.offer_code ?? lead?.recommended_offer_code ?? null,
            draft_body: draft.body,
            channel: 'email',
            rationale: extras.rationale ?? null,
            bulk_id: extras.bulk_id ?? null,
            bulk_rationale: extras.bulk_rationale ?? null,
          },
          signal,
        );
        if (res.approved) {
          setApproval(borrowerId, 'approved');
          return 'ok';
        }
        setApprovalError(`Approve failed for ${borrowerId}: endpoint returned approved=false.`);
        return 'backend';
      } catch (err: unknown) {
        if (isAbortError(err)) return 'aborted';
        const isNetwork = err instanceof ApiError && err.status === null;
        setApprovalError(
          err instanceof Error
            ? `Couldn't approve ${borrowerId}: ${err.message}`
            : `Couldn't approve ${borrowerId}.`,
        );
        return isNetwork ? 'network' : 'backend';
      } finally {
        rowInFlightRef.current[borrowerId] = false;
        setPendingApproval((p) => {
          const { [borrowerId]: _discard, ...rest } = p;
          return rest;
        });
      }
    },
    [leadsById, setApproval],
  );

  /**
   * Reject from the queue without leaving the page. Audit finding
   * 2026-04-22: this used to only mutate AppContext, so rejected
   * borrowers left no durable trace. Now calls `/api/outreach/reject`
   * which writes `mip_app.approvals` (action='reject') +
   * `mip_app.action_audit` (OUTREACH_REJECT) and fires the same
   * lifecycle-sync debounce the approve path uses.
   *
   * On failure we surface the error but do NOT flip the local state,
   * so the user can retry. Matches the approve flow's error posture.
   */
  const rejectLead = useCallback(
    async (borrowerId: string, reasonCode: RejectReasonCode, rationale: string | null = null) => {
      // R5-04: synchronous latch — see approveLead above.
      if (rowInFlightRef.current[borrowerId]) return false;
      rowInFlightRef.current[borrowerId] = true;
      setApprovalError(null);
      setPendingApproval((p) => ({ ...p, [borrowerId]: true }));
      try {
        const lead = leadsById.get(borrowerId);
        const res = await api.reject(
          borrowerId,
          {
            evidence_ids: lead?.evidence_ids ?? [],
            offer_code: lead?.recommended_offer_code ?? null,
            rationale_code: reasonCode,
            rationale,
          },
        );
        if (res.rejected) {
          setApproval(borrowerId, 'rejected');
          return true;
        } else {
          setApprovalError(`Reject failed for ${borrowerId}: endpoint returned rejected=false.`);
          return false;
        }
      } catch (err: unknown) {
        if (isAbortError(err)) return false;
        setApprovalError(
          err instanceof Error
            ? `Couldn't reject ${borrowerId}: ${err.message}`
            : `Couldn't reject ${borrowerId}.`,
        );
        return false;
      } finally {
        rowInFlightRef.current[borrowerId] = false;
        setPendingApproval((p) => {
          const { [borrowerId]: _discard, ...rest } = p;
          return rest;
        });
      }
    },
    [leadsById, setApproval],
  );

  const submitReject = useCallback(async () => {
    if (!pendingReject) return;
    const rejected = await rejectLead(pendingReject, rejectReasonCode, rejectRationale.trim() || null);
    if (rejected) {
      setPendingReject(null);
      setRejectRationale('');
      setRejectReasonCode('low_intent');
    }
  }, [pendingReject, rejectLead, rejectRationale, rejectReasonCode]);

  /**
   * Toggle one row's selection. Called by the row checkbox onChange; the
   * checkbox click is stopped from bubbling in the markup so the row
   * still expands/collapses independently.
   */
  const toggleSelect = useCallback((borrowerId: string) => {
    setSelectedIds((cur) => {
      const next = new Set(cur);
      if (next.has(borrowerId)) next.delete(borrowerId);
      else next.add(borrowerId);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  useEffect(() => {
    if (!selectedAssignee && salesTeam.length > 0) {
      setSelectedAssignee(salesTeam[0].email);
    }
  }, [salesTeam, selectedAssignee]);

  useEffect(() => {
    if (!pendingDisposition) return;
    const lead = leadsById.get(pendingDisposition);
    setDispositionLo(lead?.assigned_to_email ?? selectedAssignee ?? salesTeam[0]?.email ?? '');
    setDispositionOutcome('called_left_voicemail');
    setDispositionCallbackAt('');
    setDispositionNotes('');
  }, [leadsById, pendingDisposition, salesTeam, selectedAssignee]);

  useEffect(() => {
    if (!salesToast) return;
    const t = window.setTimeout(() => setSalesToast(null), 4000);
    return () => window.clearTimeout(t);
  }, [salesToast]);

  // Sales Manager selection is broader than approval eligibility: already
  // approved rows must still be selectable for assignment/distribution.
  // Rejected and hold rows remain locked out so operations do not
  // accidentally work a dropped or governance-held borrower.
  const selectableIds = useMemo(
    () =>
      displayLeads
        .filter((l) => isLeadSelectableForSalesOps(l.approval_status, approvals[l.borrower_id]))
        .map((l) => l.borrower_id),
    [displayLeads, approvals],
  );
  const approvalEligibleIds = useMemo(
    () =>
      displayLeads
        .filter((l) => {
          const localStatus = approvals[l.borrower_id];
          return isLeadApprovalEligible(l.approval_status, localStatus);
        })
        .map((l) => l.borrower_id),
    [displayLeads, approvals],
  );

  const applyAssignmentOverrides = useCallback((assignments: { borrower_id: string; assigned_to_email: string; assigned_to_label?: string | null; assigned_at: string; expires_at?: string | null }[]) => {
    setSalesOverrides((current) => {
      const next = { ...current };
      assignments.forEach((assignment) => {
        next[assignment.borrower_id] = {
          ...(next[assignment.borrower_id] ?? {}),
          assigned_to_email: assignment.assigned_to_email,
          assigned_to_label: assignment.assigned_to_label ?? assignment.assigned_to_email,
          assigned_at: assignment.assigned_at,
          assignment_expires_at: assignment.expires_at ?? null,
        };
      });
      return next;
    });
  }, []);

  const assignSelected = useCallback(async (mode: 'selected-lo' | 'round-robin') => {
    if (salesBusy) return;
    const borrowerIds = [...selectedIds].filter((id) => selectableIds.includes(id));
    if (borrowerIds.length === 0) return;
    const loEmails = mode === 'round-robin'
      ? salesTeam.map((member) => member.email)
      : selectedAssignee
        ? [selectedAssignee]
        : [];
    if (loEmails.length === 0) {
      setApprovalError('No active loan officers are available for assignment.');
      return;
    }
    setSalesBusy(true);
    setApprovalError(null);
    try {
      const result = borrowerIds.length === 1 && loEmails.length === 1
        ? {
            assigned_count: 1,
            assignments: [(await api.assignLead(borrowerIds[0], loEmails[0])).assignment],
          }
        : await api.distributeLeads(borrowerIds, loEmails, mode === 'round-robin' ? 'round_robin' : 'score_balanced');
      applyAssignmentOverrides(result.assignments);
      setSalesToast(`${result.assigned_count} ${result.assigned_count === 1 ? 'lead' : 'leads'} assigned`);
      setSelectedIds(new Set());
    } catch (err: unknown) {
      setApprovalError(
        err instanceof Error
          ? `Couldn't assign selected leads: ${err.message}`
          : "Couldn't assign selected leads.",
      );
    } finally {
      setSalesBusy(false);
    }
  }, [applyAssignmentOverrides, salesBusy, salesTeam, selectableIds, selectedAssignee, selectedIds]);

  const submitDisposition = useCallback(async () => {
    if (!pendingDisposition) return;
    if (!dispositionLo) {
      setApprovalError('Choose the loan officer who worked this lead.');
      return;
    }
    if (dispositionOutcome === 'callback_scheduled' && !dispositionCallbackAt) {
      setApprovalError('Callback scheduled dispositions require a callback time.');
      return;
    }
    setSalesBusy(true);
    setApprovalError(null);
    try {
      const result = await api.logDisposition(pendingDisposition, {
        lo_email: dispositionLo,
        outcome: dispositionOutcome,
        callback_at: dispositionCallbackAt ? new Date(dispositionCallbackAt).toISOString() : null,
        notes: dispositionNotes.trim() || null,
      });
      setSalesOverrides((current) => ({
        ...current,
        [pendingDisposition]: {
          ...(current[pendingDisposition] ?? {}),
          assigned_to_email: current[pendingDisposition]?.assigned_to_email ?? leadsById.get(pendingDisposition)?.assigned_to_email ?? dispositionLo,
          latest_disposition_outcome: result.disposition.outcome,
          latest_disposition_at: result.disposition.occurred_at,
          latest_callback_at: result.disposition.callback_at ?? null,
        },
      }));
      setSalesToast(`${dispositionLabel(result.disposition.outcome)} logged for ${pendingDisposition}`);
      setPendingDisposition(null);
    } catch (err: unknown) {
      setApprovalError(
        err instanceof Error
          ? `Couldn't log disposition: ${err.message}`
          : "Couldn't log disposition.",
      );
    } finally {
      setSalesBusy(false);
    }
  }, [dispositionCallbackAt, dispositionLo, dispositionNotes, dispositionOutcome, leadsById, pendingDisposition]);

  // Indeterminate state for the header checkbox: some (but not all)
  // eligible rows selected. We also reflect "all eligible selected" as
  // the checked state.
  const headerCheckboxState = useMemo(() => {
    if (selectableIds.length === 0) return { checked: false, indeterminate: false };
    const selectedEligibleCount = selectableIds.filter((id) => selectedIds.has(id)).length;
    if (selectedEligibleCount === 0) return { checked: false, indeterminate: false };
    if (selectedEligibleCount === selectableIds.length) return { checked: true, indeterminate: false };
    return { checked: false, indeterminate: true };
  }, [selectableIds, selectedIds]);

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((cur) => {
      // If any selectable rows remain unselected, select all selectable. Else
      // clear the selection.
      const allEligibleSelected =
        selectableIds.length > 0 && selectableIds.every((id) => cur.has(id));
      if (allEligibleSelected) return new Set();
      return new Set(selectableIds);
    });
  }, [selectableIds]);

  /**
   * Bulk-approve: loop `api.approve()` per selected id in chunks of
   * BULK_APPROVE_CONCURRENCY. We deliberately do NOT invent a server-side
   * bulk endpoint — the audit trail wants one row per approval.
   *
   * Successes drop out of the selection set; failures stay selected so
   * the operator can retry. A compact toast summarizes ok/fail counts.
   */
  const bulkApprove = useCallback(async () => {
    // R5-04: synchronous latch. React setState is async, so two rapid
    // clicks can both read `bulkApproving=false` before either commit
    // schedules — producing two parallel loops with the same selection
    // and two audit rows per borrower. Flip the ref before any await.
    if (bulkInFlightRef.current || bulkApproving) return;
    bulkInFlightRef.current = true;
    // Snapshot which ids to run: skip already-decided rows silently.
    const eligibleForApproval = new Set(approvalEligibleIds);
    const ids = [...selectedIds].filter((id) => eligibleForApproval.has(id));
    if (ids.length === 0) {
      bulkInFlightRef.current = false;
      return;
    }
    const bulkId = ids.length > 1 ? _newBulkId() : null;
    const sharedRationale = ids.length > 1 ? bulkRationale.trim() : '';
    if (ids.length > 1 && sharedRationale.length === 0) {
      setBulkRationaleOpen(true);
      bulkInFlightRef.current = false;
      return;
    }
    // One controller for the whole bulk loop; unmount aborts every
    // still-inflight POST. sessionStorage stashes the partial result so
    // the next mount can flash "N landed, rest aborted" — otherwise
    // the user sees no feedback that their bulk action got cut short.
    const ctrl = new AbortController();
    bulkAbortRef.current = ctrl;
    setBulkApproving(true);
    setBulkToast(null);
    let ok = 0;
    let fail = 0;
    let network = 0;
    let aborted = 0;
    // `failedIds` is only the subset safe to retry (backend/network
    // rejections — the server definitely did not commit). Aborted ids
    // stay out of this list because the server may have committed and
    // a retry would duplicate the audit row. R5-21.
    const failedIds: string[] = [];
    const abortedIds: string[] = [];
    for (const group of chunk(ids, BULK_APPROVE_CONCURRENCY)) {
      if (ctrl.signal.aborted) {
        aborted += group.length;
        abortedIds.push(...group);
        continue;
      }
      const results = await Promise.all(group.map((id) => approveLead(id, ctrl.signal, {
        bulk_id: bulkId,
        bulk_rationale: sharedRationale || null,
      })));
      results.forEach((outcome, i) => {
        if (outcome === 'ok') {
          ok += 1;
        } else if (outcome === 'aborted') {
          aborted += 1;
          abortedIds.push(group[i]);
        } else {
          fail += 1;
          if (outcome === 'network') network += 1;
          failedIds.push(group[i]);
        }
      });
    }
    if (ctrl.signal.aborted) {
      // Stash the partial result so the next mount can flash it. We
      // accept that the user may never come back to this page; the
      // alternative (loud toast on unmount) wouldn't render anyway.
      try {
        sessionStorage.setItem(
          'mip.bulkApprove.lastCancelled',
          JSON.stringify({ ok, aborted, ts: Date.now() }),
        );
      } catch {
        // private mode or quota — ignore
      }
      bulkInFlightRef.current = false;
      return;
    }
    // Quieten unused-var lint: abortedIds is tracked for future reuse
    // (R5-01 idempotency can retry by id) but not needed in this frame.
    void abortedIds;
    // Replace selection with the retryable subset so retries are
    // trivial. Aborted ids are deliberately NOT re-selected — the
    // server may have committed them and a blind re-click would
    // duplicate the audit row. R5-21 (2026-04-23).
    setSelectedIds(new Set(failedIds));
    setBulkRationaleOpen(false);
    setBulkRationale('');
    setBulkApproving(false);
    setBulkToast({ ok, fail, network, aborted });
    bulkAbortRef.current = null;
    bulkInFlightRef.current = false;
  }, [approvalEligibleIds, bulkApproving, selectedIds, approveLead, bulkRationale]);

  // On mount: if the previous mount left a partial bulk-approve snapshot
  // in sessionStorage (user navigated away mid-loop), flash a compact
  // toast so the operator knows how many landed.
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem('mip.bulkApprove.lastCancelled');
      if (!raw) return;
      sessionStorage.removeItem('mip.bulkApprove.lastCancelled');
      const parsed = JSON.parse(raw) as { ok?: number; aborted?: number; ts?: number };
      // Drop stale messages (older than 10 minutes) — they're probably
      // from a much-earlier session.
      if (parsed?.ts && Date.now() - parsed.ts > 10 * 60 * 1000) return;
      const ok = parsed.ok ?? 0;
      const aborted = parsed.aborted ?? 0;
      if (ok + aborted === 0) return;
      // R5-21: route unmounted mid-loop. Aborted ids are in ambiguous
      // state (server may have committed). Surface a "check audit log"
      // message rather than mixing them into the retryable `fail` count.
      setBulkToast({ ok, fail: 0, network: 0, aborted });
    } catch {
      // malformed payload — ignore
    }
  }, []);

  // Abort the bulk-approve loop on unmount so the remaining POSTs
  // cancel cleanly.
  useEffect(() => {
    return () => {
      bulkAbortRef.current?.abort();
    };
  }, []);

  // Auto-dismiss the toast after 4s so it doesn't pile up next to the
  // action bar.
  useEffect(() => {
    if (!bulkToast) return;
    const t = window.setTimeout(() => setBulkToast(null), 4000);
    return () => window.clearTimeout(t);
  }, [bulkToast]);

  /**
   * Keyboard: A approves / R rejects the expanded row; Shift+A fires
   * bulk-approve when >= 1 row is selected. We listen at the window
   * level but bail out if focus is inside an editable element so typing
   * in the Genie chat or a filter input never triggers approval.
   */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // R5-12 (2026-04-23): belt-and-suspenders check against both the
      // event target AND document.activeElement. For window-level
      // keydowns `e.target` is usually the focused element, but when
      // nothing is focused it falls back to `document.body` — which
      // would bypass an input check. Checking `activeElement` too
      // means typing "a" in the Genie textarea can never trigger the
      // approve hotkey.
      if (isEditableTarget(e.target as Element | null)) return;
      if (isEditableTarget(document.activeElement)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const key = e.key.toLowerCase();
      // Shift+A: bulk approve. Takes precedence over single-row A when
      // any row is selected.
      if (key === 'a' && e.shiftKey) {
        if (selectedIds.size === 0 || bulkApproving) return;
        e.preventDefault();
        void bulkApprove();
        return;
      }
      if (!expanded) return;
      const expandedLead = leadsById.get(expanded);
      const expandedStatus = approvals[expanded] ?? expandedLead?.approval_status;
      if (key === 'a') {
        if (isTerminalApproval(expandedStatus)) return;
        e.preventDefault();
        void approveLead(expanded);
      } else if (key === 'r') {
        if (isTerminalApproval(expandedStatus)) return;
        e.preventDefault();
        setPendingReject(expanded);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [expanded, approvals, approveLead, selectedIds, bulkApproving, bulkApprove, leadsById]);

  const stop = (e: ReactKeyboardEvent | React.MouseEvent) => e.stopPropagation();

  const selectionCount = selectedIds.size;
  const selectedApprovalEligibleCount = useMemo(
    () => approvalEligibleIds.filter((id) => selectedIds.has(id)).length,
    [approvalEligibleIds, selectedIds],
  );

  /**
   * Export the currently-visible leads as CSV. Client-side only: the
   * bytes come straight from the `leads` prop the caller already passed
   * in (which is the real /api/leads payload after any segment/filter
   * narrowing the parent route applied). We do NOT invent a server-side
   * export endpoint or synthesize fields the payload doesn't carry — so
   * PII stays suppressed by construction.
   */
  const exportCsv = useCallback(() => {
    if (leads.length === 0) return;
    const csv = buildLeadCsv(leads, approvals, exportContext);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const stamp = new Date().toISOString().slice(0, 10);
    a.download = `mip-leads-${stamp}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [leads, approvals, exportContext]);

  const toggleSort = useCallback((key: SortKey) => {
    if (key === 'rank') {
      setSortKey('rank');
      setSortDir('desc');
      return;
    }
    setSortKey((current) => {
      if (current === key) {
        setSortDir((dir) => (dir === 'desc' ? 'asc' : 'desc'));
        return current;
      }
      setSortDir('desc');
      return key;
    });
  }, []);

  const renderSortHeader = (key: SortKey, label: string) => (
    <button
      type="button"
      className="tbl__sort"
      onClick={() => toggleSort(key)}
      aria-label={`Sort by ${label}`}
      aria-pressed={sortKey === key}
    >
      <span>{label}</span>
      {sortKey === key && key !== 'rank' && (
        <Icon name={sortDir === 'desc' ? 'down' : 'up'} size={10} />
      )}
    </button>
  );

  return (
    // 2026-05-04 fix (alignment): removed inline `overflow: hidden`.
    // It was establishing a new block formatting context that, combined
    // with the table's intrinsic min-content width and the wrap div's
    // overflowY: auto, was nudging the surface off the .main__inner
    // left edge on the lead-queue page. The intended scroll behaviour
    // for table-containing surfaces is provided by the
    // `.surface:has(> div > .tbl) { overflow-x: auto }` rule in
    // components.css; the inline override was both unnecessary and the
    // proximate cause of the shift the user reported.
    <div className="surface">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="user" size={14} />
          </div>
          <div>
            <div className="h-4">Ranked borrowers</div>
            <div className="muted fs-12">
              {/*
                Prototype-parity-audit P2 (2026-05-04): keyboard hints
                were rendered as `.mono` spans, which read as inline code
                rather than a keycap. Switching to `<kbd>` (styled in
                design-system/components.css as a subtle keycap chip)
                makes the affordance scannable — an LO scrolling the
                queue can spot the shortcut without reading prose.
              */}
              Click a row to expand the preview. Keyboard: <kbd>A</kbd> approve, <kbd>R</kbd> reject the expanded row.
            </div>
          </div>
        </div>
        <div className="lead-table__header-actions">
          <Chip variant="neutral" icon="shield">PII suppressed</Chip>
          <Button
            size="sm"
            icon="export"
            onClick={exportCsv}
            disabled={leads.length === 0}
            data-testid="lead-export"
            aria-label={`Export ${leads.length} leads as CSV`}
          >
            Export list
          </Button>
        </div>
      </div>
      {pendingReject && (
        <form
          className="decision-panel decision-panel--inline"
          onSubmit={(e) => {
            e.preventDefault();
            void submitReject();
          }}
        >
          <div>
            <div className="h-4">Reject rationale</div>
            <div className="muted fs-12">
              Record the committee-visible reason for {pendingReject}.
            </div>
          </div>
          <label className="decision-panel__field">
            <span className="field__label">Reason</span>
            <select
              value={rejectReasonCode}
              onChange={(e) => setRejectReasonCode(e.target.value as RejectReasonCode)}
            >
              {REJECT_REASONS.map((reason) => (
                <option key={reason.code} value={reason.code}>{reason.label}</option>
              ))}
            </select>
          </label>
          <label className="decision-panel__field decision-panel__field--wide">
            <span className="field__label">Rationale note</span>
            <textarea
              value={rejectRationale}
              onChange={(e) => setRejectRationale(e.target.value)}
              maxLength={500}
              placeholder="Optional unless reason is Other."
            />
          </label>
          <div className="decision-panel__actions">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setPendingReject(null);
                setRejectRationale('');
                setRejectReasonCode('low_intent');
              }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              icon="cross"
              disabled={rejectReasonCode === 'other_with_text' && rejectRationale.trim().length === 0}
            >
              Confirm reject
            </Button>
          </div>
        </form>
      )}
      {pendingDisposition && (
        <form
          className="decision-panel decision-panel--inline"
          onSubmit={(e) => {
            e.preventDefault();
            void submitDisposition();
          }}
        >
          <div>
            <div className="h-4">Call disposition</div>
            <div className="muted fs-12">
              Log LO activity for {pendingDisposition}.
            </div>
          </div>
          <label className="decision-panel__field">
            <span className="field__label">Loan officer</span>
            <select
              value={dispositionLo}
              onChange={(e) => setDispositionLo(e.target.value)}
              required
            >
              <option value="" disabled>Choose LO</option>
              {salesTeam.map((member) => (
                <option key={member.email} value={member.email}>
                  {member.display_label} · {member.email}
                </option>
              ))}
            </select>
          </label>
          <label className="decision-panel__field">
            <span className="field__label">Outcome</span>
            <select
              value={dispositionOutcome}
              onChange={(e) => setDispositionOutcome(e.target.value as CallDisposition['outcome'])}
            >
              {DISPOSITION_OPTIONS.map((option) => (
                <option key={option.outcome} value={option.outcome}>{option.label}</option>
              ))}
            </select>
          </label>
          {dispositionOutcome === 'callback_scheduled' && (
            <label className="decision-panel__field">
              <span className="field__label">Callback time</span>
              <input
                type="datetime-local"
                value={dispositionCallbackAt}
                onChange={(e) => setDispositionCallbackAt(e.target.value)}
                required
              />
            </label>
          )}
          <label className="decision-panel__field decision-panel__field--wide">
            <span className="field__label">Notes</span>
            <textarea
              value={dispositionNotes}
              onChange={(e) => setDispositionNotes(e.target.value)}
              maxLength={500}
              placeholder="Optional operational note; no borrower PII."
            />
          </label>
          <div className="decision-panel__actions">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setPendingDisposition(null)}
              disabled={salesBusy}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              icon="bolt"
              disabled={salesBusy || !dispositionLo}
            >
              {salesBusy ? 'Logging…' : 'Log disposition'}
            </Button>
          </div>
        </form>
      )}
      {salesToast && (
        <div role="status" aria-live="polite" className="table-success">
          {salesToast}
        </div>
      )}
      <div className="tbl-wrap" tabIndex={0} aria-label="Ranked borrowers table scroll region">
        <table className="tbl lead-table__table">
          <colgroup>
            <col className="lead-table__col-select" />
            <col className="lead-table__col-expand" />
            <col className="lead-table__col-borrower" />
            <col className="lead-table__col-location" />
            <col className="lead-table__col-relationship" />
            <col className="lead-table__col-assignment" />
            <col className="lead-table__col-outreach" />
            <col className="lead-table__col-disposition" />
            <col className="lead-table__col-segments" />
            <col className="lead-table__col-equity" />
            <col className="lead-table__col-rate" />
            <col className="lead-table__col-offer" />
            <col className="lead-table__col-score" />
            <col className="lead-table__col-confidence" />
            <col className="lead-table__col-approval" />
          </colgroup>
          <thead>
            <tr>
              <th className="tbl-cell--select">
                <input
                  type="checkbox"
                  aria-label="Select all eligible leads"
                  checked={headerCheckboxState.checked}
                  ref={(el) => {
                    if (el) el.indeterminate = headerCheckboxState.indeterminate;
                  }}
                  disabled={selectableIds.length === 0 || bulkApproving}
                  onChange={toggleSelectAll}
                  onClick={stop}
                  data-testid="lead-select-all"
                />
              </th>
              <th className="tbl-cell--narrow"></th>
              <th>Borrower</th>
              <th>Location</th>
              <th>{renderSortHeader('relationship', 'Relationship')}</th>
              <th>{renderSortHeader('assignment', 'Assigned to')}</th>
              <th>{renderSortHeader('outreach', 'Outreach')}</th>
              <th>Last touch</th>
              <th>Segments</th>
              <th className="tbl-cell--right">{renderSortHeader('equity', 'Equity')}</th>
              <th className="tbl-cell--right">{renderSortHeader('rate', 'Rate Δ (bps)')}</th>
              <th>Next-best-offer</th>
              <th className="tbl-cell--right">{renderSortHeader('score', 'Score')}</th>
              <th>{renderSortHeader('confidence', 'Confidence')}</th>
              <th>Approval</th>
            </tr>
          </thead>
          <tbody>
            {sortedLeads.map((lead) => {
              const isOpen = expanded === lead.borrower_id;
              // Prefer in-session AppContext override (set optimistically on
              // approve/reject); fall back to the server-projected
              // approval_status so a page reload doesn't make approved
              // borrowers look pending. Round-2 hole-finder #12, 2026-04-23.
              const serverStatus = lead.approval_status;
              const approval: string | undefined = approvals[lead.borrower_id]
                ?? (isTerminalApproval(serverStatus)
                    ? serverStatus
                    : undefined);
              const isSelected = selectedIds.has(lead.borrower_id);
              const isSelectable = isLeadSelectableForSalesOps(serverStatus, approval);
              return (
                <Fragment key={lead.borrower_id}>
                  <tr
                    className={isOpen ? 'is-expanded' : ''}
                    tabIndex={0}
                    role="button"
                    aria-expanded={isOpen}
                    aria-label={`Lead ${lead.borrower_id}, ${isOpen ? 'expanded' : 'collapsed'}. Press Enter or Space to toggle preview; A to approve, R to reject.`}
                    onClick={() => {
                      setLastBorrowerId(lead.borrower_id);
                      setExpanded(isOpen ? null : lead.borrower_id);
                    }}
                    onKeyDown={(e) => {
                      // R5-10 (2026-04-23): make rows toggleable from
                      // the keyboard so A/R hotkeys work without a
                      // prior mouse click. We only intercept when the
                      // focus target is the row itself — if focus is
                      // on the nested checkbox or approve button,
                      // their own handlers run and we bail.
                      if (e.target !== e.currentTarget) return;
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setLastBorrowerId(lead.borrower_id);
                        setExpanded(isOpen ? null : lead.borrower_id);
                      }
                    }}
                  >
                    <td className="tbl-cell--select" onClick={stop}>
                      <input
                        type="checkbox"
                        aria-label={`Select lead ${lead.borrower_id}`}
                        checked={isSelected}
                        disabled={!isSelectable || bulkApproving}
                        onChange={() => toggleSelect(lead.borrower_id)}
                        onClick={stop}
                        data-testid={`lead-select-${lead.borrower_id}`}
                      />
                    </td>
                    <td>
                      <Icon name={isOpen ? 'down' : 'chevright'} size={14} className="muted" />
                    </td>
                    <td className="is-primary">
                      <div className="mono lead-table__borrower">{lead.borrower_id}</div>
                      <div className="mono muted lead-table__clip">
                        {lead.clip && lead.clip.length > 0
                          ? lead.clip
                          : 'property_ref_unavailable'}
                      </div>
                    </td>
                    <td>
                      {lead.city}, {lead.state}
                      <div className="muted mono lead-table__zip">{lead.zip}</div>
                    </td>
                    <td>
                      <div className="chip-stack">
                        <Chip variant={relationshipVariant(lead)}>
                          {relationshipLabel(lead)}
                        </Chip>
                        {lead.marketing_eligible === false && (
                          <Chip variant="warning">
                            Suppressed{lead.suppression_reason ? `: ${lead.suppression_reason}` : ''}
                          </Chip>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="chip-stack">
                        <Chip variant={lead.assigned_to_email ? 'success' : 'neutral'}>
                          {lead.assigned_to_label ?? lead.assigned_to_email ?? 'Unassigned'}
                        </Chip>
                        {lead.assigned_at && (
                          <span className="muted mono fs-11">{formatDateTimeShort(lead.assigned_at)}</span>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="chip-stack">
                        <Chip variant={outreachVariant(lead.outreach_status)}>
                          {outreachLabel(lead.outreach_status)}
                        </Chip>
                        {typeof lead.aging_days === 'number' && lead.aging_days > 7 && (
                          <Chip variant="warning">{lead.aging_days}d aging</Chip>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="chip-stack lead-table__last-touch" onClick={stop}>
                        <Chip variant={dispositionVariant(lead.latest_disposition_outcome)}>
                          {dispositionLabel(lead.latest_disposition_outcome)}
                        </Chip>
                        {lead.latest_disposition_at && (
                          <span className="muted mono fs-11">{formatDateTimeShort(lead.latest_disposition_at)}</span>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          icon="bolt"
                          onClick={(e) => {
                            e.stopPropagation();
                            setPendingDisposition(lead.borrower_id);
                          }}
                          disabled={salesBusy || salesTeam.length === 0}
                          aria-label={`Log call disposition for ${lead.borrower_id}`}
                        >
                          Log
                        </Button>
                      </div>
                    </td>
                    <td>
                      <div className="lead-table__segments">
                        {lead.segment_codes.slice(0, 2).map((sid) => {
                          const color = segmentColor(sid);
                          return (
                            <span
                              key={sid}
                              className="chip chip--segment chip--compact"
                              style={{ '--chip-hue': color } as CSSProperties}
                            >
                              {segmentName(sid)}
                            </span>
                          );
                        })}
                        {lead.segment_codes.length > 2 && (
                          <span className="chip chip--neutral chip--compact">
                            +{lead.segment_codes.length - 2}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="num tbl-cell--right">${(lead.equity_estimate / 1000).toFixed(0)}k</td>
                    <td
                      className={`num tbl-cell--right ${lead.rate_spread_bps >= 75 ? 'lead-table__rate--positive' : 'lead-table__rate--neutral'}`}
                    >
                      +{lead.rate_spread_bps}
                    </td>
                    <td>
                      <span className="mono fs-12 text-1">{lead.recommended_offer}</span>{' '}
                      <EvidenceChip source={DRAWER_SOURCES.nbo}>{DRAWER_SOURCES.nbo.short}</EvidenceChip>
                    </td>
                    <td className="tbl-cell--right"><ScoreBadge value={lead.opportunity_score} /></td>
                    <td><ConfidenceMeter value={lead.confidence} compact /></td>
                    <td
                      className="tbl-cell--approval"
                      data-testid={`lead-approval-cell-${lead.borrower_id}`}
                    >
                      {approval === 'approved' && <Chip variant="success" icon="check">Approved</Chip>}
                      {approval === 'rejected' && <Chip variant="danger" icon="cross">Rejected</Chip>}
                      {approval === 'hold' && <Chip variant="warning" icon="shield">Hold</Chip>}
                      {!approval && (
                        <div
                          className="lead-table__approval-actions"
                          onClick={stop}
                        >
                          <Button
                            variant="primary"
                            size="sm"
                            icon="check"
                            disabled={Boolean(pendingApproval[lead.borrower_id])}
                            onClick={(e) => {
                              e.stopPropagation();
                              void approveLead(lead.borrower_id);
                            }}
                            aria-label={`Approve ${lead.borrower_id}`}
                            data-testid={`lead-approve-${lead.borrower_id}`}
                          >
                            {pendingApproval[lead.borrower_id] ? 'Approving…' : 'Approve'}
                          </Button>
                          <button
                            type="button"
                            className="btn btn--sm lead-table__reject"
                            aria-label={`Reject ${lead.borrower_id}`}
                            title="Reject"
                            disabled={Boolean(pendingApproval[lead.borrower_id])}
                            onClick={(e) => {
                              e.stopPropagation();
                              setPendingReject(lead.borrower_id);
                            }}
                            data-testid={`lead-reject-${lead.borrower_id}`}
                          >
                            <Icon name="cross" size={12} />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="tbl__expand">
                      <td colSpan={15}>
                        <RowPreview lead={lead} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {selectionCount > 0 && (
        <div
          role="toolbar"
          aria-label="Bulk actions"
          data-testid="lead-bulk-actions"
          className="bulk-actions"
        >
          <div className="bulk-actions__label">
            <span className="mono num">{selectionCount}</span> {selectionCount === 1 ? 'lead' : 'leads'} selected
          </div>
          {selectionCount > 1 && bulkRationaleOpen && (
            <label className="bulk-actions__rationale">
              <span className="field__label">Shared approval rationale</span>
              <input
                value={bulkRationale}
                onChange={(e) => setBulkRationale(e.target.value)}
                maxLength={500}
                placeholder="Example: Q3 retention sweep, all reviewed against current rules."
              />
            </label>
          )}
          <div className="bulk-actions__controls">
            {salesTeam.length > 0 && (
              <>
                <label className="bulk-actions__assignee">
                  <span className="field__label">Assign to</span>
                  <select
                    value={selectedAssignee}
                    onChange={(e) => setSelectedAssignee(e.target.value)}
                    disabled={salesBusy}
                    aria-label="Loan officer assignment target"
                  >
                    {salesTeam.map((member) => (
                      <option key={member.email} value={member.email}>
                        {member.display_label}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  variant="default"
                  size="sm"
                  icon="user"
                  onClick={() => void assignSelected('selected-lo')}
                  disabled={salesBusy || !selectedAssignee || selectionCount === 0}
                  aria-label={`Assign ${selectionCount} selected leads to selected loan officer`}
                >
                  {salesBusy ? 'Assigning…' : 'Assign'}
                </Button>
                <Button
                  variant="default"
                  size="sm"
                  icon="layers"
                  onClick={() => void assignSelected('round-robin')}
                  disabled={salesBusy || salesTeam.length === 0 || selectionCount === 0}
                  aria-label={`Distribute ${selectionCount} selected leads across active loan officers`}
                >
                  Distribute
                </Button>
              </>
            )}
            {bulkToast && (
              <span
                role="status"
                aria-live="polite"
                data-testid="lead-bulk-toast"
                className={`bulk-actions__toast ${
                  bulkToast.aborted > 0 || bulkToast.network > 0
                    ? 'bulk-actions__toast--danger'
                    : bulkToast.fail > 0
                      ? 'bulk-actions__toast--warn'
                      : 'bulk-actions__toast--ok'
                }`}
              >
                {bulkToast.ok} approved
                {bulkToast.fail > 0 ? `, ${bulkToast.fail} failed` : ''}
                {bulkToast.network > 0
                  ? ` (${bulkToast.network} network dropped — retry)`
                  : ''}
                {/*
                  R5-21: aborted rows are in an ambiguous state — the
                  client cancelled the POST but the server may have
                  committed. Do NOT encourage retry; direct the user to
                  the audit log instead. TODO: once R5-01 (server-side
                  idempotency) lands, retry becomes safe.
                */}
                {bulkToast.aborted > 0
                  ? ` · ${bulkToast.aborted} cancelled in flight — unknown state, check the audit log`
                  : ''}
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={clearSelection}
              disabled={bulkApproving}
              data-testid="lead-bulk-clear"
            >
              Clear selection
            </Button>
            <Button
              variant="primary"
              size="sm"
              icon={bulkApproving ? undefined : 'check'}
              onClick={() => void bulkApprove()}
              disabled={bulkApproving || selectedApprovalEligibleCount === 0}
              data-testid="lead-bulk-approve"
              aria-label={`Approve ${selectedApprovalEligibleCount} eligible leads`}
            >
              {bulkApproving ? 'Approving…' : `Approve ${selectedApprovalEligibleCount} eligible`}
            </Button>
          </div>
        </div>
      )}
      {approvalError && (
        <div
          role="alert"
          className="table-error"
        >
          {approvalError}
        </div>
      )}
      <div className="surface__ft">
        Showing {leads.length.toLocaleString()} ranked borrower{leads.length === 1 ? '' : 's'}
        {totalMatching !== null && (
          <>
            {' '}of {totalMatching.toLocaleString()} total matching filters
          </>
        )}
        {truncatedAt !== null && totalMatching !== null && totalMatching > leads.length && (
          <span className="muted"> · capped at {truncatedAt.toLocaleString()}</span>
        )}
      </div>
    </div>
  );
}
