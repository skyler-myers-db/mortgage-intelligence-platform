import { Fragment, useCallback, useEffect, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent } from 'react';
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
 */

function RowPreview({ lead }: { lead: LeadSummary }) {
  return (
    <div className="tbl__expand-inner" style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr 1fr', gap: 20 }}>
      <div>
        <div className="eyebrow" style={{ marginBottom: 8 }}>Customer 360 preview</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <Cell k="CLIP"          v={`clip_${lead.borrower_id.toLowerCase().replace('-', '')}`} mono />
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

  /**
   * Approve from the queue without leaving the page. Uses the same
   * `/api/outreach/approve` endpoint Offer Orchestrator calls. We mark
   * the row as 'approved' in AppContext optimistically on success so the
   * chip flips immediately and stays flipped on route change.
   */
  const approveLead = useCallback(
    async (borrowerId: string) => {
      setApprovalError(null);
      setPendingApproval((p) => ({ ...p, [borrowerId]: true }));
      try {
        const res = await api.approve(borrowerId);
        if (res.approved) {
          setApproval(borrowerId, 'approved');
        } else {
          setApprovalError(`Approve failed for ${borrowerId}: endpoint returned approved=false.`);
        }
      } catch (err: unknown) {
        setApprovalError(
          err instanceof Error
            ? `Couldn't approve ${borrowerId}: ${err.message}`
            : `Couldn't approve ${borrowerId}.`,
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

  const rejectLead = useCallback(
    (borrowerId: string) => {
      setApprovalError(null);
      setApproval(borrowerId, 'rejected');
    },
    [setApproval],
  );

  /**
   * Keyboard: A approves / R rejects the expanded row. We listen at the
   * window level but bail out if focus is inside an editable element so
   * typing in the Genie chat or a filter input never triggers approval.
   */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!expanded) return;
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (target.isContentEditable || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
          return;
        }
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key === 'a') {
        if (approvals[expanded] === 'approved') return;
        e.preventDefault();
        void approveLead(expanded);
      } else if (key === 'r') {
        if (approvals[expanded] === 'rejected') return;
        e.preventDefault();
        rejectLead(expanded);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [expanded, approvals, approveLead, rejectLead]);

  const stop = (e: ReactKeyboardEvent | React.MouseEvent) => e.stopPropagation();

  return (
    <div className="surface" style={{ overflow: 'hidden' }}>
      <div className="surface__hdr" style={{ justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 28, height: 28, borderRadius: 8, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'grid', placeItems: 'center' }}>
            <Icon name="user" size={14} />
          </div>
          <div>
            <div className="h-4">Ranked borrowers · drill to evidence</div>
            <div className="muted" style={{ fontSize: 12 }}>
              Click any row for the Cotality evidence trail — CLIP, Owner Link, and lien history.
              {' '}Keyboard: <span className="mono">A</span> to approve, <span className="mono">R</span> to reject the expanded row.
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Chip variant="neutral" icon="shield">PII suppressed · compliance</Chip>
          <Button size="sm" icon="export">Export list</Button>
        </div>
      </div>
      <div style={{ maxHeight: 520, overflowY: 'auto' }}>
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ paddingLeft: 20, width: 32 }}></th>
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
              return (
                <Fragment key={lead.borrower_id}>
                  <tr
                    className={isOpen ? 'is-expanded' : ''}
                    onClick={() => setExpanded(isOpen ? null : lead.borrower_id)}
                  >
                    <td style={{ paddingLeft: 20 }}>
                      <Icon name={isOpen ? 'down' : 'chevright'} size={14} className="muted" />
                    </td>
                    <td className="is-primary">
                      <div className="mono" style={{ fontSize: 12, color: 'var(--text-1)' }}>{lead.borrower_id}</div>
                      <div className="mono muted" style={{ fontSize: 10 }}>
                        {`clip_${lead.borrower_id.toLowerCase().replace(/-/g, '')}`}
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
                            onClick={(e) => {
                              e.stopPropagation();
                              rejectLead(lead.borrower_id);
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
                      <td colSpan={10}>
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
        Showing {leads.length} borrowers · <span className="mono">SELECT * FROM mip.gold.lead_scores WHERE segment IN (…)</span>
      </div>
    </div>
  );
}
