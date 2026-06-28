import { Button, Chip } from '../components/Primitives';
import type { GrowthAgentMonitor } from '../types';
import { formatGrowthAgentCount } from './ask-genie.growth-run-card';

interface SavedGrowthAgentMonitorsProps {
  monitors: GrowthAgentMonitor[];
  monitorPending: string | null;
  actionsDisabled: boolean;
  onRun: (monitor: GrowthAgentMonitor) => void;
  onOpen: (route: string) => void;
}

export function SavedGrowthAgentMonitors({
  monitors,
  monitorPending,
  actionsDisabled,
  onRun,
  onOpen,
}: SavedGrowthAgentMonitorsProps) {
  if (monitors.length === 0) return null;

  return (
    <div className="growth-agent-monitors" aria-label="Saved Growth Agent watchlists">
      <div className="eyebrow">Saved watchlists</div>
      <div className="growth-agent-monitor-list">
        {monitors.map((monitor) => {
          const inactive = monitor.status !== 'active';
          return (
            <div
              key={monitor.monitor_id}
              className="growth-agent-monitor"
            >
              <div className="growth-agent-monitor__main">
                <span>{monitor.name}</span>
                <span>{monitor.cadence === 'weekly' ? 'Weekly review' : 'Daily review'}</span>
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
    </div>
  );
}
