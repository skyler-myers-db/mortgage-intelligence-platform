import { Fragment, useCallback, useEffect, useMemo, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import type { LeadSummary } from '../../types';
import { Icon } from '../Icon';
import { Chip, Button, EvidenceChip } from '../Primitives';
import { ScoreBadge } from './ScoreBadge';
import { ConfidenceMeter } from './ConfidenceMeter';
import { useApp } from '../AppContext';
import { api } from '../../lib/api';
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

function RowPreview({ lead }: { lead: LeadSummary }) {
  // Prefer the real Cotality CLIP projected by the backend (2026-04-22
  // contract addition). Fall back to a borrower-id derived placeholder
  // only for rows predating that projection (empty string).
  const clipValue = lead.clip && lead.clip.length > 0
    ? lead.clip
    : `clip_${lead.borrower_id.toLowerCase().replace('-', '')}`;
  return (
    <div className="tbl__expand-inner" style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr 1fr', gap: 20 }}>
      <div>
        <div className="eyebrow" style={{ marginBottom: 8 }}>Customer 360 preview</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <Cell k="CLIP"          v={clipValue} mono />
          <Cell k="Location"      v={`${lead.city}, ${lead.state} · ${lead.zip}`} />
          <Cell k="Equity"        v={`$${(lead.equity_estimate / 1000).toFixed(0)}k`} mono />
          <Cell k="Rate spread"   v={`+${lead.rate_spread_bps} bps`} mono />
          <Cell k="Score"         v={`${lead.opportunity_score}`} mono />
          <Cell k="Confidence"    v={`${lead.confidence}%`} mono />
        </div>
        <div className="eyebrow" style={{ marginTop: 18, marginBottom: 8 }}>Segments</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {lead.segment_codes.map((sid) => {
            const color = segmentColor(sid);
            const style: CSSProperties = {
              color,
              background: `color-mix(in oklab, ${color} 14%, transparent)`,
              borderColor: `color-mix(in oklab, ${color} 35%, transparent)`,
            };
            return (
              <span key={sid} className="chip" style={style}>
                {segmentName(sid)}
              </span>
            );
          })}
        </div>
      </div>

      <div>
        <div className="eyebrow" style={{ marginBottom: 8 }}>Why now</div>
        <p className="body" style={{ marginTop: 0 }}>{lead.why_now}</p>
        <div style={{ marginTop: 12, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="muted" style={{ fontSize: 11 }}>Evidence:</span>
          <EvidenceChip source={DRAWER_SOURCES.itm}>Rate + equity ruleset</EvidenceChip>
          <EvidenceChip source={DRAWER_SOURCES.nbo}>Next-best-offer model</EvidenceChip>
        </div>
      </div>

      <div>
        <div className="eyebrow" style={{ marginBottom: 8 }}>Next-best-offer</div>
        <div className="surface" style={{ padding: '14px 16px', background: 'var(--bg-1)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em' }}>{lead.recommended_offer}</div>
            <ScoreBadge value={lead.opportunity_score} />
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            Confidence <ConfidenceMeter value={lead.confidence} compact />
          </div>
          <div style={{ marginTop: 12 }}>
            <Link className="btn btn--primary btn--sm" to={`/borrower-360/${lead.borrower_id}`}>
              Open Borrower 360
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

function Cell({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 11 }}>{k}</div>
      <div className={mono ? 'mono num' : ''} style={{ fontSize: 13, color: 'var(--text-1)' }}>{v}</div>
    </div>
  );
}

export function LeadTable({ leads }: { leads: LeadSummary[] }) {
  const [expanded, setExpanded] = useState<string | null>(leads[0]?.borrower_id ?? null);
  const { approvals, setApproval } = useApp();
  const [pendingApproval, setPendingApproval] = useState<Record<string, boolean>>({});
  const [approvalError, setApprovalError] = useState<string | null>(null);
  // Bulk-approve state. `selectedIds` is a Set so toggling is O(1); we
  // copy-on-write when updating to keep React's reference check happy.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [bulkApproving, setBulkApproving] = useState<boolean>(false);
  // Last bulk result surfaced as a compact toast. Clears on the next bulk
  // run or when the user dismisses it (auto-dismiss after 4s).
  const [bulkToast, setBulkToast] = useState<{ ok: number; fail: number } | null>(null);

  /**
   * Approve from the queue without leaving the page. Uses the same
   * `/api/outreach/approve` endpoint Offer Orchestrator calls. We mark
   * the row as 'approved' in AppContext optimistically on success so the
   * chip flips immediately and stays flipped on route change.
   *
   * Returns `true` on success and `false` on failure so the bulk-approve
   * caller can tally ok/fail without threading the error string through.
   */
  const approveLead = useCallback(
    async (borrowerId: string): Promise<boolean> => {
      setApprovalError(null);
      setPendingApproval((p) => ({ ...p, [borrowerId]: true }));
      try {
        const res = await api.approve(borrowerId);
        if (res.approved) {
          setApproval(borrowerId, 'approved');
          return true;
        }
        setApprovalError(`Approve failed for ${borrowerId}: endpoint returned approved=false.`);
        return false;
      } catch (err: unknown) {
        setApprovalError(
          err instanceof Error
            ? `Couldn't approve ${borrowerId}: ${err.message}`
            : `Couldn't approve ${borrowerId}.`,
        );
        return false;
      } finally {
        setPendingApproval((p) => {
          const { [borrowerId]: _discard, ...rest } = p;
          return rest;
        });
      }
    },
    [setApproval],
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
    async (borrowerId: string) => {
      setApprovalError(null);
      setPendingApproval((p) => ({ ...p, [borrowerId]: true }));
      try {
        const res = await api.reject(borrowerId);
        if (res.rejected) {
          setApproval(borrowerId, 'rejected');
        } else {
          setApprovalError(`Reject failed for ${borrowerId}: endpoint returned rejected=false.`);
        }
      } catch (err: unknown) {
        setApprovalError(
          err instanceof Error
            ? `Couldn't reject ${borrowerId}: ${err.message}`
            : `Couldn't reject ${borrowerId}.`,
        );
      } finally {
        setPendingApproval((p) => {
          const { [borrowerId]: _discard, ...rest } = p;
          return rest;
        });
      }
    },
    [setApproval],
  );

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

  // IDs that are actually eligible for bulk approval: not already approved
  // or rejected. The "select all" header checkbox targets this subset so
  // already-decided rows don't get bulk-approved again.
  const eligibleIds = useMemo(
    () => leads.map((l) => l.borrower_id).filter((id) => !approvals[id]),
    [leads, approvals],
  );

  // Indeterminate state for the header checkbox: some (but not all)
  // eligible rows selected. We also reflect "all eligible selected" as
  // the checked state.
  const headerCheckboxState = useMemo(() => {
    if (eligibleIds.length === 0) return { checked: false, indeterminate: false };
    const selectedEligibleCount = eligibleIds.filter((id) => selectedIds.has(id)).length;
    if (selectedEligibleCount === 0) return { checked: false, indeterminate: false };
    if (selectedEligibleCount === eligibleIds.length) return { checked: true, indeterminate: false };
    return { checked: false, indeterminate: true };
  }, [eligibleIds, selectedIds]);

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((cur) => {
      // If any eligible rows remain unselected, select all eligible. Else
      // clear the selection.
      const allEligibleSelected =
        eligibleIds.length > 0 && eligibleIds.every((id) => cur.has(id));
      if (allEligibleSelected) return new Set();
      return new Set(eligibleIds);
    });
  }, [eligibleIds]);

  /**
   * Bulk-approve: loop `api.approve()` per selected id in chunks of
   * BULK_APPROVE_CONCURRENCY. We deliberately do NOT invent a server-side
   * bulk endpoint — the audit trail wants one row per approval.
   *
   * Successes drop out of the selection set; failures stay selected so
   * the operator can retry. A compact toast summarizes ok/fail counts.
   */
  const bulkApprove = useCallback(async () => {
    if (bulkApproving) return;
    // Snapshot which ids to run: skip already-decided rows silently.
    const ids = [...selectedIds].filter((id) => !approvals[id]);
    if (ids.length === 0) return;
    setBulkApproving(true);
    setBulkToast(null);
    let ok = 0;
    let fail = 0;
    const failedIds: string[] = [];
    for (const group of chunk(ids, BULK_APPROVE_CONCURRENCY)) {
      const results = await Promise.all(group.map((id) => approveLead(id)));
      results.forEach((success, i) => {
        if (success) ok += 1;
        else {
          fail += 1;
          failedIds.push(group[i]);
        }
      });
    }
    // Replace selection with the failed subset so retries are trivial.
    setSelectedIds(new Set(failedIds));
    setBulkApproving(false);
    setBulkToast({ ok, fail });
  }, [bulkApproving, selectedIds, approvals, approveLead]);

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
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (target.isContentEditable || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
          return;
        }
      }
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
      if (key === 'a') {
        if (approvals[expanded] === 'approved') return;
        e.preventDefault();
        void approveLead(expanded);
      } else if (key === 'r') {
        if (approvals[expanded] === 'rejected') return;
        e.preventDefault();
        void rejectLead(expanded);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [expanded, approvals, approveLead, rejectLead, selectedIds, bulkApproving, bulkApprove]);

  const stop = (e: ReactKeyboardEvent | React.MouseEvent) => e.stopPropagation();

  const selectionCount = selectedIds.size;

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
    const header = [
      'borrower_id',
      'clip',
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
    ];
    const escape = (v: string): string =>
      /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
    const rows = leads.map((l) =>
      [
        l.borrower_id,
        l.clip ?? '',
        l.city,
        l.state,
        l.zip,
        l.segment_codes.join('|'),
        String(l.equity_estimate),
        String(l.rate_spread_bps),
        String(l.opportunity_score),
        String(l.confidence),
        l.recommended_offer,
        approvals[l.borrower_id] ?? l.approval_status ?? 'pending',
      ]
        .map(escape)
        .join(','),
    );
    const csv = [header.join(','), ...rows].join('\n');
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
  }, [leads, approvals]);

  return (
    <div className="surface" style={{ overflow: 'hidden' }}>
      <div className="surface__hdr" style={{ justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 28, height: 28, borderRadius: 8, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'grid', placeItems: 'center' }}>
            <Icon name="user" size={14} />
          </div>
          <div>
            <div className="h-4">Ranked borrowers</div>
            <div className="muted" style={{ fontSize: 12 }}>
              Click a row to expand the preview. Keyboard: <span className="mono">A</span> approve, <span className="mono">R</span> reject the expanded row.
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
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
      <div style={{ maxHeight: 520, overflowY: 'auto', position: 'relative' }}>
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ paddingLeft: 20, width: 32 }}>
                <input
                  type="checkbox"
                  aria-label="Select all eligible leads"
                  checked={headerCheckboxState.checked}
                  ref={(el) => {
                    if (el) el.indeterminate = headerCheckboxState.indeterminate;
                  }}
                  disabled={eligibleIds.length === 0 || bulkApproving}
                  onChange={toggleSelectAll}
                  onClick={stop}
                  data-testid="lead-select-all"
                />
              </th>
              <th style={{ width: 32 }}></th>
              <th>Borrower</th>
              <th>Location</th>
              <th>Segments</th>
              <th style={{ textAlign: 'right' }}>Equity</th>
              <th style={{ textAlign: 'right' }}>Rate Δ (bps)</th>
              <th>Next-best-offer</th>
              <th style={{ textAlign: 'right' }}>Score</th>
              <th>Confidence</th>
              <th>Approval</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((lead) => {
              const isOpen = expanded === lead.borrower_id;
              const approval = approvals[lead.borrower_id];
              const isSelected = selectedIds.has(lead.borrower_id);
              const isEligible = !approval;
              return (
                <Fragment key={lead.borrower_id}>
                  <tr
                    className={isOpen ? 'is-expanded' : ''}
                    onClick={() => setExpanded(isOpen ? null : lead.borrower_id)}
                  >
                    <td style={{ paddingLeft: 20 }} onClick={stop}>
                      <input
                        type="checkbox"
                        aria-label={`Select lead ${lead.borrower_id}`}
                        checked={isSelected}
                        disabled={!isEligible || bulkApproving}
                        onChange={() => toggleSelect(lead.borrower_id)}
                        onClick={stop}
                        data-testid={`lead-select-${lead.borrower_id}`}
                      />
                    </td>
                    <td>
                      <Icon name={isOpen ? 'down' : 'chevright'} size={14} className="muted" />
                    </td>
                    <td className="is-primary">
                      <div className="mono" style={{ fontSize: 12, color: 'var(--text-1)' }}>{lead.borrower_id}</div>
                      <div className="mono muted" style={{ fontSize: 10 }}>
                        {lead.clip && lead.clip.length > 0
                          ? lead.clip
                          : `clip_${lead.borrower_id.toLowerCase().replace(/-/g, '')}`}
                      </div>
                    </td>
                    <td>
                      {lead.city}, {lead.state}
                      <div className="muted mono" style={{ fontSize: 11 }}>{lead.zip}</div>
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {lead.segment_codes.slice(0, 2).map((sid) => {
                          const color = segmentColor(sid);
                          const style: CSSProperties = {
                            padding: '2px 6px',
                            fontSize: 10,
                            color,
                            background: `color-mix(in oklab, ${color} 12%, transparent)`,
                            borderColor: `color-mix(in oklab, ${color} 30%, transparent)`,
                          };
                          return (
                            <span key={sid} className="chip" style={style}>
                              {segmentName(sid)}
                            </span>
                          );
                        })}
                        {lead.segment_codes.length > 2 && (
                          <span className="chip chip--neutral" style={{ padding: '2px 6px', fontSize: 10 }}>
                            +{lead.segment_codes.length - 2}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="num" style={{ textAlign: 'right' }}>${(lead.equity_estimate / 1000).toFixed(0)}k</td>
                    <td
                      className="num"
                      style={{
                        textAlign: 'right',
                        color: lead.rate_spread_bps >= 75 ? 'var(--signal-success)' : 'var(--text-2)',
                      }}
                    >
                      +{lead.rate_spread_bps}
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize: 12, color: 'var(--text-1)' }}>{lead.recommended_offer}</span>{' '}
                      <EvidenceChip source={DRAWER_SOURCES.nbo}>nbo_v3</EvidenceChip>
                    </td>
                    <td style={{ textAlign: 'right' }}><ScoreBadge value={lead.opportunity_score} /></td>
                    <td><ConfidenceMeter value={lead.confidence} compact /></td>
                    <td
                      style={{ paddingRight: 16 }}
                      data-testid={`lead-approval-cell-${lead.borrower_id}`}
                    >
                      {approval === 'approved' && <Chip variant="success" icon="check">Approved</Chip>}
                      {approval === 'rejected' && <Chip variant="danger" icon="cross">Rejected</Chip>}
                      {!approval && (
                        <div
                          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
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
                            className="btn btn--sm"
                            aria-label={`Reject ${lead.borrower_id}`}
                            title="Reject"
                            disabled={Boolean(pendingApproval[lead.borrower_id])}
                            onClick={(e) => {
                              e.stopPropagation();
                              void rejectLead(lead.borrower_id);
                            }}
                            data-testid={`lead-reject-${lead.borrower_id}`}
                            style={{ padding: '4px 8px' }}
                          >
                            <Icon name="cross" size={12} />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="tbl__expand">
                      <td colSpan={11}>
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
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            padding: '10px 16px',
            borderTop: '1px solid var(--line-1)',
            background: 'var(--bg-1)',
            position: 'sticky',
            bottom: 0,
            zIndex: 2,
          }}
        >
          <div style={{ fontSize: 13, color: 'var(--text-1)' }}>
            <span className="mono num">{selectionCount}</span> {selectionCount === 1 ? 'lead' : 'leads'} selected
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {bulkToast && (
              <span
                role="status"
                aria-live="polite"
                data-testid="lead-bulk-toast"
                style={{
                  fontSize: 12,
                  color: bulkToast.fail > 0 ? 'var(--signal-warning)' : 'var(--signal-success)',
                }}
              >
                {bulkToast.ok} approved, {bulkToast.fail} failed
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
              disabled={bulkApproving || selectionCount === 0}
              data-testid="lead-bulk-approve"
              aria-label={`Approve ${selectionCount} leads`}
            >
              {bulkApproving ? 'Approving…' : `Approve ${selectionCount} leads`}
            </Button>
          </div>
        </div>
      )}
      {approvalError && (
        <div
          role="alert"
          style={{
            padding: '8px 16px',
            color: 'var(--signal-danger)',
            fontSize: 12,
            borderTop: '1px solid var(--line-1)',
          }}
        >
          {approvalError}
        </div>
      )}
      <div className="surface__ft">
        Showing {leads.length.toLocaleString()} borrower{leads.length === 1 ? '' : 's'}
      </div>
    </div>
  );
}
