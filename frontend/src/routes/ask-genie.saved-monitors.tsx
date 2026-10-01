import { Button, Chip } from '../components/Primitives';
import type { GrowthAgentMonitor } from '../types';
import type { GrowthAgentSchedulerState } from '../types/growthAgent';
import { SCHEDULED_RUN_STATUS } from '../lib/growthAgentSchedulerCopy';
import { formatGrowthAgentCount } from './ask-genie.growth-run-card';

/**
 * deviation:growth-agent-scheduled-run-status: each row says whether scheduled
 * runs are on, from the scheduler job's real state (audit 2026-09-21
 * flow-08 slice 2), instead of a hard-coded paused string. The copy lives in
 * lib/growthAgentSchedulerCopy (Home's watchlist briefings read it too).
 */
export { SCHEDULED_RUN_STATUS };

interface SavedGrowthAgentMonitorsProps {
  monitors: GrowthAgentMonitor[];
  /** A missing state means unavailable. */
  schedulerState?: GrowthAgentSchedulerState;
  monitorPending: string | null;
  draftPending: string | null;
  actionsDisabled: boolean;
  onRun: (monitor: GrowthAgentMonitor) => void;
  onDraft: (monitor: GrowthAgentMonitor) => void;
  onOpen: (route: string) => void;
}

export function SavedGrowthAgentMonitors({
  monitors,
  schedulerState = 'unavailable',
  monitorPending,
  draftPending,
  actionsDisabled,
  onRun,
  onDraft,
  onOpen,
}: SavedGrowthAgentMonitorsProps) {
  if (monitors.length === 0) return null;

  return (
    <section className="growth-agent-monitors" aria-label="Saved Growth Agent watchlists">
      <div className="eyebrow">Saved watchlists</div>
      <div className="growth-agent-monitor-list">
        {monitors.map((monitor) => {
          const inactive = monitor.status !== 'active';
          const cannotDraft = inactive || !monitor.last_run_id;
          return (
            <div
              key={monitor.monitor_id}
              className="growth-agent-monitor"
            >
              <div className="growth-agent-monitor__main">
                <span>{monitor.name}</span>
                <span>
                  {monitor.cadence === 'weekly' ? 'Weekly interval' : 'Daily interval'}
                  {` · ${SCHEDULED_RUN_STATUS[schedulerState]}`}
                </span>
                <Chip variant={inactive ? 'warning' : 'success'}>
                  {monitor.status === 'active'
                    ? 'Active'
                    : monitor.status === 'paused'
                      ? 'Paused'
                      : 'Disabled'}
                </Chip>
              </div>
              <strong>{formatGrowthAgentCount(monitor.actionable_total)}</strong>
              <div className="growth-agent-monitor__actions">
                <Button
                  variant="ghost"
                  size="sm"
                  icon="play"
                  onClick={() => {
                    if (!inactive) onRun(monitor);
                  }}
                  disabled={actionsDisabled || monitorPending !== null || inactive}
                  aria-disabled={actionsDisabled || monitorPending !== null || inactive}
                  title={inactive ? 'Only active watchlists can be rerun.' : undefined}
                >
                  {monitorPending === monitor.monitor_id ? 'Running…' : 'Run now'}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  icon="doc"
                  onClick={() => {
                    if (!cannotDraft) onDraft(monitor);
                  }}
                  disabled={actionsDisabled || draftPending !== null || cannotDraft}
                  aria-disabled={actionsDisabled || draftPending !== null || cannotDraft}
                  title={cannotDraft ? 'Drafts require an active watchlist with a completed run.' : undefined}
                >
                  {draftPending === monitor.monitor_id ? 'Drafting…' : 'Draft Slack/Teams'}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  icon="chevright"
                  onClick={() => onOpen(monitor.route)}
                >
                  Open
                </Button>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
