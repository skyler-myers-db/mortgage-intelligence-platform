import { Button } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { GrowthAgentRunSlot } from './ask-genie.growth-agent-run-slot';
import type { GrowthAgentWorkspace } from './ask-genie.growth-agent-state';
import { SavedGrowthAgentMonitors } from './ask-genie.saved-monitors';

interface GrowthAgentMonitorsPanelProps {
  agent: GrowthAgentWorkspace;
  onOpenRoute: (route: string) => void;
  /** Switch to the Workflows tab (the empty state's way forward). */
  onOpenWorkflows: () => void;
}

/**
 * The Saved monitors tab of `/ask-genie` (audit 2026-09-21 `visual-07` /
 * `genie-09`): saved watchlists, their re-run and draft actions, and the
 * progress / result of an action started here. The list component is
 * unchanged; it only moved out of the Growth Agent surface.
 *
 * No `aria-busy` on this surface: it would hold the run slot's in-progress
 * `role="status"` announcement until the run ends, when that card is gone.
 * The monitor buttons already say Running… and are disabled while busy.
 */
export function GrowthAgentMonitorsPanel({ agent, onOpenRoute, onOpenWorkflows }: GrowthAgentMonitorsPanelProps) {
  const { monitors } = agent;
  const empty = monitors.length === 0 && !agent.workflowsLoading;
  return (
    <div className="surface growth-agent">
      <div className="surface__hdr">
        <Icon name="bell" size={14} className="icon-accent" />
        <div>
          <h2 className="h-4">Saved monitors</h2>
          <div className="muted fs-12">
            Watchlists you saved from a workflow. Re-run one to refresh its count, or draft a Slack or Teams note for
            review. Nothing is sent automatically.
          </div>
        </div>
      </div>
      <div className="surface__body">
        {agent.workflowsError && (
          <div className="status-callout status-callout--danger mt-3" role="alert">
            Could not load saved watchlists.
          </div>
        )}
        <GrowthAgentRunSlot agent={agent} origin="monitors" onOpenRoute={onOpenRoute} />
        {empty && !agent.workflowsError && (
          <div className="surface surface--inset">
            <div className="surface__body genie-empty">
              <div className="genie-empty__icon">
                <Icon name="bell" size={16} />
              </div>
              <div>
                <div className="genie-empty__title">No saved monitors yet.</div>
                <p className="genie-empty__copy">
                  Use Save watchlist on any workflow to keep its filters here and re-run them later.
                </p>
                <Button variant="ghost" size="sm" icon="chevright" onClick={onOpenWorkflows}>
                  Open workflows
                </Button>
              </div>
            </div>
          </div>
        )}
        <SavedGrowthAgentMonitors
          monitors={monitors}
          monitorPending={agent.monitorPending}
          draftPending={agent.monitorDraftPending}
          actionsDisabled={agent.agentBusy}
          onRun={agent.rerunGrowthAgentMonitor}
          onDraft={agent.draftGrowthAgentMonitorNotifications}
          onOpen={onOpenRoute}
        />
      </div>
    </div>
  );
}
