import { useEffect } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { useApp, type Accent, type Density, type Theme } from '../AppContext';
import { Icon, type IconName } from '../Icon';
import { Chip } from '../Primitives';
import { PropertyLookupPanel } from '../mortgage/PropertyLookupPanel';
import { api, type ActorAuditEventSummary } from '../../lib/api';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { formatTimeOfDay, formatTimestamp } from '../../lib/time';

/**
 * Console — the right-side tweaks panel from the prototype. Theme, accent,
 * density, evidence / signal-strength toggles, configured tenant. Opens from the
 * topbar tweak icon. Uses `.tweaks` BEM from the prototype so a single class
 * controls positioning + animation.
 */

const ACCENT_SWATCHES: Accent[] = ['bright', 'teal', 'navy', 'red'];
const RECENT_ACTIVITY_PAGE_SIZE = 8;

const ACTIVITY_LABELS: Record<string, string> = {
  APPROVE: 'Outreach approved',
  OUTREACH_APPROVE: 'Outreach approved',
  OUTREACH_REJECT: 'Outreach rejected',
  REJECT: 'Outreach rejected',
  DRAFT_OUTREACH: 'Outreach draft created',
  SAVE_DRAFT: 'Outreach draft saved',
  DELETE_DRAFT: 'Outreach draft removed',
  SAVE_LEAD: 'Lead saved',
  UNSAVE_LEAD: 'Lead removed',
  LEAD_ASSIGN: 'Lead assigned',
  LEAD_DISTRIBUTE: 'Leads distributed',
  CALL_DISPOSITION: 'Call disposition recorded',
  LEAD_OUTCOME: 'Lead outcome recorded',
  LEAD_OUTCOME_RECORDED: 'Lead outcome recorded',
  PORTFOLIO_CREATE: 'Portfolio created',
  RECOMMEND_OFFER: 'Offer recommended',
  RUN_GENIE: 'Genie analysis run',
  VIEW_BORROWER: 'Borrower reviewed',
  VIEW_LEADS: 'Lead queue reviewed',
};

export function recentActivityPresentation(event: ActorAuditEventSummary): {
  label: string;
  icon: IconName;
  tone: string;
  context: string;
} {
  const fallback = event.event_type
    .toLowerCase()
    .split('_')
    .filter(Boolean)
    .map((word, index) => index === 0 ? `${word.charAt(0).toUpperCase()}${word.slice(1)}` : word)
    .join(' ');
  const rejected = event.event_type.includes('REJECT');
  const approved = event.event_type.includes('APPROVE');
  const icon: IconName = rejected
    ? 'cross'
    : approved
      ? 'check'
      : event.event_type.includes('GENIE')
        ? 'sparkle'
        : 'audit';
  const entity = event.entity_type.replace(/_/g, ' ');
  return {
    label: ACTIVITY_LABELS[event.event_type] ?? (fallback || 'Activity recorded'),
    icon,
    tone: rejected ? 'red' : approved ? 'green' : '',
    context: event.subject_id ?? entity,
  };
}

export function Console() {
  const {
    consoleOpen, setConsoleOpen,
    theme, setTheme,
    accent, setAccent,
    density, setDensity,
    lender,
    showEvidence, setShowEvidence,
    showConfidence, setShowConfidence,
    setGenieOpen,
    savedLeads,
    savedDrafts,
    workspaceStatus,
    workspaceError,
    refreshWorkspace,
    recentActivityFocusRequest,
    acknowledgeRecentActivityFocus,
  } = useApp();
  const activityQuery = useInfiniteQuery({
    queryKey: ['audit', 'my-events', RECENT_ACTIVITY_PAGE_SIZE],
    queryFn: ({ signal, pageParam }) => (
      api.myAuditEvents(RECENT_ACTIVITY_PAGE_SIZE, signal, pageParam)
    ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: consoleOpen,
    retry: false,
    refetchOnMount: 'always',
    gcTime: 0,
  });
  const recentActivity = activityQuery.data?.pages.flatMap((page) => page.items) ?? [];

  useEffect(() => {
    if (!consoleOpen || recentActivityFocusRequest < 1) return;
    const section = document.getElementById('console-recent-activity');
    if (!section) return;
    section.focus();
    section.scrollIntoView?.({ block: 'nearest' });
    acknowledgeRecentActivityFocus();
  }, [acknowledgeRecentActivityFocus, consoleOpen, recentActivityFocusRequest]);

  const savedLeadItems = Object.values(savedLeads)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 4);
  const savedDraftItems = Object.values(savedDrafts)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 3);
  const savedLeadCount = Object.keys(savedLeads).length;
  const savedDraftCount = Object.keys(savedDrafts).length;

  return (
    <aside
      id="workspace-console"
      className={`tweaks ${consoleOpen ? 'is-open' : ''}`}
      role="complementary"
      aria-label="Workspace console"
      aria-hidden={!consoleOpen}
      tabIndex={-1}
    >
      <div className="tweaks__hdr">
        <Icon name="tweak" size={14} className="tweaks__hdr-icon" />
        <div className="tweaks__title">Console</div>
        <button
          className="drawer__close"
          onClick={() => setConsoleOpen(false)}
          aria-label="Close console"
          type="button"
        >
          <Icon name="close" size={14} />
        </button>
      </div>
      <div className="tweaks__body">
        <div className="tweak-row">
          <label>Theme</label>
          <div className="segmented" role="group" aria-label="Theme">
            {(['dark', 'light'] as Theme[]).map((t) => (
              <button
                key={t}
                className={theme === t ? 'is-active' : ''}
                onClick={() => setTheme(t)}
                type="button"
                aria-pressed={theme === t}
              >
                {t === 'dark' ? 'Dark' : 'Light'}
              </button>
            ))}
          </div>
        </div>
        <div className="tweak-row">
          <label>Accent</label>
          <div className="swatches">
            {ACCENT_SWATCHES.map((a) => (
              <button
                key={a}
                className={`sw sw--${a} ${accent === a ? 'is-active' : ''}`}
                onClick={() => setAccent(a)}
                aria-label={`Accent ${a}`}
                type="button"
                aria-pressed={accent === a}
              />
            ))}
          </div>
        </div>
        <div className="tweak-row">
          <label>Density</label>
          <div className="segmented" role="group" aria-label="Density">
            {(['comfortable', 'compact'] as Density[]).map((d) => (
              <button
                key={d}
                className={density === d ? 'is-active' : ''}
                onClick={() => setDensity(d)}
                type="button"
                aria-pressed={density === d}
              >
                {d === 'comfortable' ? 'Comfortable' : 'Compact'}
              </button>
            ))}
          </div>
        </div>
        <div className="tweak-row">
          <label>Property lookup</label>
          <PropertyLookupPanel compact onNavigate={() => setConsoleOpen(false)} />
        </div>
        <div
          id="console-recent-activity"
          className="tweak-row"
          tabIndex={-1}
        >
          <label>Recent activity</label>
          <div className="surface">
            <div className="surface__hdr">
              <Icon name="audit" size={14} className="tweaks__hdr-icon" />
              <div className="h-4">My recent activity</div>
              <div className="topbar__spacer" />
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => void activityQuery.refetch()}
                disabled={activityQuery.isFetching}
              >
                Refresh
              </button>
            </div>
            <div className="audit-panel" aria-live="polite">
              {activityQuery.isPending && (
                <div className="audit-panel__pad audit-panel__message">Loading recent activity…</div>
              )}
              {activityQuery.isError && (
                <div className="audit-panel__pad stack-sm">
                  <div className="audit-panel__message audit-panel__message--danger">
                    Recent activity is unavailable.
                  </div>
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => void activityQuery.refetch()}
                  >
                    Retry
                  </button>
                </div>
              )}
              {activityQuery.isSuccess && recentActivity.length === 0 && (
                <div className="audit-panel__pad audit-panel__message">No recent activity yet.</div>
              )}
              {recentActivity.map((event, index) => {
                const presentation = recentActivityPresentation(event);
                return (
                  <div
                    className="audit"
                    key={`${event.created_at}-${event.event_type}-${index}`}
                  >
                    <time
                      className="audit__time mono"
                      dateTime={event.created_at}
                      title={formatTimestamp(event.created_at, { withSeconds: true })}
                    >
                      {formatTimeOfDay(event.created_at)}
                    </time>
                    <div className={`audit__ico ${presentation.tone}`}>
                      <Icon name={presentation.icon} size={11} />
                    </div>
                    <div className="audit__body">
                      <div className="audit__what">{presentation.label}</div>
                      <div className="audit__who">{presentation.context}</div>
                    </div>
                  </div>
                );
              })}
            </div>
            {activityQuery.hasNextPage && (
              <div className="surface__ft">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => void activityQuery.fetchNextPage()}
                  disabled={activityQuery.isFetchingNextPage}
                >
                  {activityQuery.isFetchingNextPage ? 'Loading…' : 'Load older'}
                </button>
              </div>
            )}
          </div>
        </div>
        <div className="tweak-row">
          <label>Configured tenant</label>
          <div className="stack-sm">
            <Chip variant="neutral" icon="building">{lender}</Chip>
            <div className="muted fs-12">
              Read-only in Module 0; lender configuration is applied server-side.
            </div>
          </div>
        </div>
        <div className="tweak-row">
          <label>Saved workspace</label>
          <div className="saved-workspace">
            <div className="saved-workspace__summary">
              <span>{savedLeadCount} saved lead{savedLeadCount === 1 ? '' : 's'}</span>
              <span>{savedDraftCount} draft{savedDraftCount === 1 ? '' : 's'}</span>
            </div>
            {workspaceStatus === 'loading' && (
              <div className="muted fs-12">Loading Lakebase workspace…</div>
            )}
            {workspaceError && (
              <div className="stack-sm">
                <div className="text-danger fs-12">{workspaceError}</div>
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={refreshWorkspace}
                >
                  Retry workspace
                </button>
              </div>
            )}
            {workspaceStatus !== 'loading' && savedLeadItems.length === 0 && savedDraftItems.length === 0 && (
              <div className="muted fs-12">No saved leads or drafts yet.</div>
            )}
            {savedLeadItems.map((lead) => (
              <Link
                key={`lead-${lead.borrower_id}`}
                className="saved-workspace__item"
                to={`/borrower-360/${lead.borrower_id}`}
                onClick={() => setConsoleOpen(false)}
              >
                <Icon name="tag" size={12} />
                <span className="saved-workspace__body">
                  <span className="mono">{lead.borrower_id}</span>
                  <span>{lead.city}, {lead.state} · {offerDisplayLabel(null, lead.recommended_offer)}</span>
                </span>
              </Link>
            ))}
            {savedDraftItems.map((draft) => (
              <Link
                key={`draft-${draft.borrower_id}-${draft.channel}`}
                className="saved-workspace__item"
                to={`/offer-orchestrator/${draft.borrower_id}`}
                onClick={() => setConsoleOpen(false)}
              >
                <Icon name="doc" size={12} />
                <span className="saved-workspace__body">
                  <span className="mono">{draft.borrower_id}</span>
                  <span>Draft saved · {draft.channel}</span>
                </span>
              </Link>
            ))}
          </div>
        </div>
        <div className="tweak-row">
          <div className="row">
            <label>Show evidence chips</label>
            <button
              className={`switch ${showEvidence ? 'on' : ''}`}
              onClick={() => setShowEvidence(!showEvidence)}
              aria-pressed={showEvidence}
              aria-label="Toggle evidence chips"
              type="button"
            />
          </div>
        </div>
        <div className="tweak-row">
          <div className="row">
            <label>Show signal meters</label>
            <button
              className={`switch ${showConfidence ? 'on' : ''}`}
              onClick={() => setShowConfidence(!showConfidence)}
              aria-pressed={showConfidence}
              aria-label="Toggle signal strength meters"
              type="button"
            />
          </div>
        </div>
        <div className="tweak-row">
          <div className="row">
            <label>Ask Genie</label>
            <button
              className="btn btn--sm"
              onClick={() => { setGenieOpen(true); setConsoleOpen(false); }}
              type="button"
            >
              Open Genie
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}
