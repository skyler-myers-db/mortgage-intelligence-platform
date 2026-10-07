import { Suspense, useEffect } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { useApp, type Accent, type Density } from '../AppContext';
import { Icon } from '../Icon';
import { Chip, SurfaceTitle } from '../Primitives';
import { PropertyLookupPanel } from '../mortgage/PropertyLookupPanel';
import { LazyLenderMark } from './lenderMarkLoader';
import { ThemePreferenceControl } from './ThemePreferenceControl';
import { api } from '../../lib/api';
import { recentActivityPresentation } from '../../lib/auditEventPresentation';
import { useSingleKeyShortcuts } from '../../lib/keymapPreference';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { preloadIntentProps } from '../../lib/preloadIntent';
import { lenderMarkUrl } from '../../lib/themePreference';
import { formatTimeOfDay, formatTimestamp } from '../../lib/time';

/**
 * Console — the right-side tweaks panel from the prototype. Theme, accent,
 * density, evidence / signal-strength toggles, configured tenant. Opens from the
 * topbar tweak icon. Uses `.tweaks` BEM from the prototype so a single class
 * controls positioning + animation.
 *
 * Row captions (deviation:console-row-captions; audit a11y-05): the
 * prototype captions every `.tweak-row` with a `<label>` tied to no control
 * (design_files/Module 0 Prototype.html:1442-1470). A `<label>` must name a
 * control, and labelling a button would rename it ("Open Genie"), so a
 * caption is a `<span class="tweak-row__label">` styled exactly as the
 * prototype label; it names its group (Accent, Density) by aria-labelledby.
 * The three switch rows keep a real `<label htmlFor>` on the switch, whose
 * aria-label / aria-labelledby still give its accessible name.
 */

const ACCENT_SWATCHES: Accent[] = ['bright', 'teal', 'navy', 'red'];
const RECENT_ACTIVITY_PAGE_SIZE = 8;

// The activity labels and their presentation live in lib/auditEventPresentation
// (shared with the borrower decision history); re-exported for this module's tests.
export { recentActivityPresentation };

export function Console() {
  const {
    consoleOpen, setConsoleOpen,
    accent, setAccent,
    density, setDensity,
    lender,
    sessionStatus,
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
  const [singleKeyShortcuts, setSingleKeyShortcuts] = useSingleKeyShortcuts();
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
    // A named <aside> is the complementary landmark: no redundant role.
    <aside
      id="workspace-console"
      className={`tweaks ${consoleOpen ? 'is-open' : ''}`}
      aria-label="Workspace console"
      aria-hidden={!consoleOpen}
      inert={!consoleOpen}
      tabIndex={-1}
    >
      <div className="tweaks__hdr">
        <Icon name="tweak" size={14} className="tweaks__hdr-icon" />
        {/* The Console's own h2, so its group titles (h3) nest under it rather
            than under the page's last surface (a11y-03). */}
        <h2 className="tweaks__title">Console</h2>
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
          <span className="tweak-row__label" id="console-theme-label">Theme</span>
          <ThemePreferenceControl />
        </div>
        <div className="tweak-row">
          <span className="tweak-row__label" id="console-accent-label">Accent</span>
          <div className="swatches" role="group" aria-labelledby="console-accent-label">
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
          <span className="tweak-row__label" id="console-density-label">Density</span>
          <div className="segmented" role="group" aria-labelledby="console-density-label">
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
          <span className="tweak-row__label" id="console-lookup-label">Property lookup</span>
          <PropertyLookupPanel compact headingLevel={3} onNavigate={() => setConsoleOpen(false)} />
        </div>
        <div
          id="console-recent-activity"
          className="tweak-row"
          tabIndex={-1}
        >
          <span className="tweak-row__label" id="console-activity-label">Recent activity</span>
          <div className="surface">
            <div className="surface__hdr">
              <Icon name="audit" size={14} className="tweaks__hdr-icon" />
              <SurfaceTitle level={3}>My recent activity</SurfaceTitle>
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
          <span className="tweak-row__label" id="console-tenant-label">Configured tenant</span>
          <div className="stack-sm">
            <Chip
              variant="neutral"
              leading={lenderMarkUrl() ? (
                <Suspense fallback={<Icon name="building" size={10} />}>
                  <LazyLenderMark iconSize={10} sessionLender={sessionStatus === 'ready' ? lender : null} />
                </Suspense>
              ) : (
                <Icon name="building" size={10} />
              )}
            >
              {lender}
            </Chip>
            <div className="muted fs-12">
              Read-only in Module 0; lender configuration is applied server-side.
            </div>
          </div>
        </div>
        <div className="tweak-row">
          <span className="tweak-row__label" id="console-workspace-label">Saved workspace</span>
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
            {/* Intent warms the route CHUNK only (bundle-09 item 1): the
                dossier and offer reads are audited, so they stay click-only. */}
            {savedLeadItems.map((lead) => (
              <Link
                key={`lead-${lead.borrower_id}`}
                className="saved-workspace__item"
                to={`/borrower-360/${lead.borrower_id}`}
                onClick={() => setConsoleOpen(false)}
                {...preloadIntentProps(`/borrower-360/${lead.borrower_id}`)}
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
                {...preloadIntentProps(`/offer-orchestrator/${draft.borrower_id}`)}
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
            <label htmlFor="console-evidence-switch">Show evidence chips</label>
            <button
              id="console-evidence-switch"
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
            <label htmlFor="console-signal-switch">Show signal meters</label>
            <button
              id="console-signal-switch"
              className={`switch ${showConfidence ? 'on' : ''}`}
              onClick={() => setShowConfidence(!showConfidence)}
              aria-pressed={showConfidence}
              aria-label="Toggle signal strength meters"
              type="button"
            />
          </div>
        </div>
        {/* WCAG 2.1.4: every shortcut without Ctrl / Cmd / Alt can be switched
            off per actor (lib/keymapPreference); ⌘K stays on. */}
        <div className="tweak-row">
          <div className="row">
            <label id="console-single-key-label" htmlFor="console-single-key-switch">Single-key shortcuts</label>
            <button
              id="console-single-key-switch"
              className={`switch ${singleKeyShortcuts ? 'on' : ''}`}
              onClick={() => setSingleKeyShortcuts(!singleKeyShortcuts)}
              aria-pressed={singleKeyShortcuts}
              aria-labelledby="console-single-key-label"
              aria-describedby="console-single-key-note"
              type="button"
              data-testid="console-single-key-shortcuts"
            />
          </div>
          <div id="console-single-key-note" className="muted fs-12">
            J, K, A, R, X, / and ? on their own, and the table's arrow keys and Enter. ⌘K / Ctrl+K always works.
          </div>
        </div>
        <div className="tweak-row">
          <div className="row">
            <span className="tweak-row__label" id="console-genie-label">Ask Genie</span>
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
