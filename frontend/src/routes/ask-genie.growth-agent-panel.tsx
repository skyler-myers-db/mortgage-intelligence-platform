import { Button, Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { humanizeAssetMentions } from '../lib/assetLabels';
import type { GrowthAgentCadence, GrowthAgentSegmentMode } from '../types';
import { GrowthAgentRunSlot } from './ask-genie.growth-agent-run-slot';
import type { GrowthAgentWorkspace } from './ask-genie.growth-agent-state';
import {
  CUSTOM_SEGMENTS,
  renderSourceAssetChip,
  workflowIcon,
} from './ask-genie.growth-agent.helpers';

interface GrowthAgentPanelProps {
  agent: GrowthAgentWorkspace;
  onOpenRoute: (route: string) => void;
}

/**
 * The Workflows tab of `/ask-genie`: the Mortgage Growth Agent's objective
 * box, state scope and review interval, the reviewed workflow cards and the
 * custom segment builder. Moved out of `routes/ask-genie.tsx` (file-size
 * gate); state and handlers live in `useGrowthAgentWorkspace`.
 *
 * Audit 2026-09-21 `genie-09`: the run slot (progress, error, result,
 * composed plan) sits directly under the controls, above the cards, so a run
 * reports where it runs instead of below the custom builder. Saved
 * watchlists and their drafts moved to the Saved monitors tab.
 */
export function GrowthAgentPanel({ agent, onOpenRoute }: GrowthAgentPanelProps) {
  const {
    agentBusy,
    stateParsePreview,
    workflows,
    growthAgentPending,
    growthAgentPendingAction,
    promptAgentPending,
    promptAgentPendingAction,
    composePending,
    customAgentPendingAction,
    customSegments,
  } = agent;

  return (
    <div className="surface growth-agent" aria-busy={agentBusy}>
      <div className="surface__hdr">
        <Icon name="bolt" size={14} className="icon-accent" />
        <div>
          <h2 className="h-4">Mortgage growth co-pilot</h2>
          <div className="muted fs-12">
            Describe a borrower-growth goal and the Mortgage Growth Agent picks a reviewed workflow, counts the
            eligible borrowers and opens them in the Lead Queue for review. Nothing is sent without human approval.
          </div>
        </div>
      </div>
      <div className="surface__body">
        <section className="growth-agent-command" aria-label="Mortgage Growth Agent command center">
          <div className="growth-agent-command__main">
            <label className="growth-agent__field">
              <span>Growth objective</span>
              <textarea
                className="route-textarea growth-agent-command__prompt"
                aria-label="Mortgage Growth Agent prompt"
                value={agent.agentPrompt}
                onChange={(event) => {
                  agent.setAgentPrompt(event.target.value);
                  agent.clearGrowthAgentFeedback();
                }}
              />
              <span className="growth-agent__hint">
                Describe the borrower-growth objective in plain language — the co-pilot turns it into a reviewed workflow.
              </span>
            </label>
          </div>
          <div className="growth-agent-command__actions">
            <Button
              variant="primary"
              size="sm"
              icon="sparkle"
              onClick={() => agent.runMortgageGrowthAgentPrompt(false)}
              disabled={agentBusy || stateParsePreview.invalid.length > 0}
            >
              {promptAgentPending && promptAgentPendingAction === 'run' ? 'Planning…' : 'Plan reviewed workflow'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon="bell"
              onClick={() => agent.runMortgageGrowthAgentPrompt(true)}
              disabled={agentBusy || stateParsePreview.invalid.length > 0}
            >
              {promptAgentPending && promptAgentPendingAction === 'save' ? 'Saving…' : 'Save reviewed watchlist'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon="sparkle"
              onClick={() => agent.composeGrowthAgentPlan(false)}
              disabled={agentBusy || stateParsePreview.invalid.length > 0}
            >
              {composePending === 'compose' ? 'Composing…' : 'Compose plan'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon="bolt"
              onClick={() => agent.composeGrowthAgentPlan(true)}
              disabled={agentBusy || stateParsePreview.invalid.length > 0}
            >
              {composePending === 'execute' ? 'Executing…' : 'Execute plan'}
            </Button>
          </div>
        </section>

        <div className="growth-agent__controls">
          <label className="growth-agent__field">
            <span>State scope</span>
            <input
              className="form-input"
              aria-label="Growth Agent state scope"
              placeholder="All states or IL, TX, CA"
              value={agent.agentStateText}
              onChange={(event) => {
                const nextValue = event.target.value;
                agent.setAgentStateText(nextValue);
                agent.clearGrowthAgentFeedback();
              }}
            />
            <span className="growth-agent__hint">
              {stateParsePreview.invalid.length > 0
                ? `Invalid: ${stateParsePreview.invalid.join(', ')}`
                : stateParsePreview.states.length > 0
                  ? `Scoped to ${stateParsePreview.states.join(', ')}`
                  : 'No state scope; the workflow uses the current coverage footprint.'}
            </span>
          </label>
          <label className="growth-agent__field growth-agent__field--compact">
                <span>Review interval</span>
                <select
                  className="form-input"
                  aria-label="Growth Agent review interval"
                  value={agent.agentCadence}
              onChange={(event) => {
                agent.setAgentCadence(event.target.value as GrowthAgentCadence);
                agent.clearGrowthAgentFeedback();
              }}
            >
                  <option value="daily">Daily interval</option>
                  <option value="weekly">Weekly interval</option>
                </select>
                <span className="growth-agent__hint">Saved watchlists stay paused until an admin turns on scheduled runs.</span>
          </label>
        </div>

        {agent.workflowsError && (
          <div className="status-callout status-callout--danger mt-3" role="alert">
            Could not load Growth Agent workflows.
          </div>
        )}

        <GrowthAgentRunSlot agent={agent} origin="workflows" onOpenRoute={onOpenRoute} />

        <section className="growth-agent__cards" aria-label="Governed Growth Agent workflows">
          {workflows.map((workflow) => {
            const pending = growthAgentPending === workflow.id;
            return (
              <article key={workflow.id} className="growth-agent-card">
                <div className="growth-agent-card__head">
                  <span className="growth-agent-card__icon">
                    <Icon name={workflowIcon(workflow.id)} size={16} />
                  </span>
                  <div>
                    <div className="growth-agent-card__title">{workflow.title}</div>
                    <div className="growth-agent-card__trigger">{workflow.trigger_label}</div>
                  </div>
                </div>
                <p className="growth-agent-card__copy">{humanizeAssetMentions(workflow.objective)}</p>
                <div className="growth-agent-card__proof">
                  {workflow.proof_points.slice(0, 3).map((point) => (
                    <div key={point} className="growth-agent-card__proof-line">
                      <Icon name="check" size={11} />
                      <span>{humanizeAssetMentions(point)}</span>
                    </div>
                  ))}
                </div>
                <div className="chip-row growth-agent-card__assets">
                  {workflow.source_assets.slice(0, 3).map((asset) => renderSourceAssetChip(asset))}
                </div>
                <div className="growth-agent-card__actions">
                  <Button
                    variant="primary"
                    size="sm"
                    icon="play"
                    onClick={() => agent.runGrowthAgentWorkflow(workflow, false)}
                    disabled={agentBusy || stateParsePreview.invalid.length > 0}
                  >
                    {pending && growthAgentPendingAction === 'run' ? 'Running…' : 'Run'}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon="bell"
                    onClick={() => agent.runGrowthAgentWorkflow(workflow, true)}
                    disabled={agentBusy || stateParsePreview.invalid.length > 0}
                  >
                    {pending && growthAgentPendingAction === 'save' ? 'Saving…' : 'Save watchlist'}
                  </Button>
                </div>
              </article>
            );
          })}
          {agent.workflowsLoading && (
            <div className="surface surface--inset growth-agent-card growth-agent-card--loading">
              <div className="surface__body">Loading workflows…</div>
            </div>
          )}
        </section>

        <section className="growth-agent-custom" aria-label="Build a custom Growth Agent workflow">
          <div className="growth-agent-custom__head">
            <Icon name="filter" size={14} className="icon-accent" />
            <div>
              <div className="h-4">Build a custom segment workflow</div>
              <div className="muted fs-12">
                Combine reviewed borrower segments, count the borrowers eligible for outreach, then save the filters as a watchlist.
              </div>
            </div>
            <div className="spacer" />
            <Chip variant="neutral" icon="shield">Reviewed segments only</Chip>
          </div>
          <div className="growth-agent-custom__body">
            <div className="chip-row" role="group" aria-label="Custom workflow segments">
              {CUSTOM_SEGMENTS.map((segment) => {
                const selected = customSegments.includes(segment.code);
                return (
                  <button
                    key={segment.code}
                    type="button"
                    className={`filter ${selected ? 'is-active' : ''}`}
                    aria-pressed={selected}
                    onClick={() => agent.toggleCustomSegment(segment.code)}
                  >
                    <span className="filter__value">{segment.label}</span>
                  </button>
                );
              })}
            </div>
            <label className="growth-agent__field growth-agent__field--compact">
              <span>Segment logic</span>
              <select
                className="form-input"
                aria-label="Custom Growth Agent segment logic"
                value={agent.customMode}
                onChange={(event) => {
                  agent.setCustomMode(event.target.value as GrowthAgentSegmentMode);
                  agent.clearGrowthAgentFeedback();
                }}
              >
                <option value="any">Any selected segment</option>
                <option value="all">All selected segments</option>
              </select>
              <span className="growth-agent__hint">
                Any counts each borrower once across the selected segments; All requires every selected segment.
              </span>
            </label>
            <div className="growth-agent-card__actions">
              <Button
                variant="primary"
                size="sm"
                icon="play"
                onClick={() => agent.runCustomGrowthAgentWorkflow(false)}
                disabled={agentBusy || customSegments.length === 0 || stateParsePreview.invalid.length > 0}
              >
                {growthAgentPending === 'custom_segment_watch' && customAgentPendingAction === 'run' ? 'Running…' : 'Run custom'}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                icon="bell"
                onClick={() => agent.runCustomGrowthAgentWorkflow(true)}
                disabled={agentBusy || customSegments.length === 0 || stateParsePreview.invalid.length > 0}
              >
                {growthAgentPending === 'custom_segment_watch' && customAgentPendingAction === 'save' ? 'Saving…' : 'Save custom watchlist'}
              </Button>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
