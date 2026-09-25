import { useEffect, useRef } from 'react';
import { usePrefersReducedMotion } from '../lib/usePrefersReducedMotion';
import { ComposePlanCard } from './ask-genie.compose-plan-card';
import { GrowthAgentDraftPanel } from './ask-genie.growth-agent-drafts';
import type { GrowthAgentRunOrigin, GrowthAgentWorkspace } from './ask-genie.growth-agent-state';
import { renderSourceAssetChip } from './ask-genie.growth-agent.helpers';
import { GrowthAgentRunCard } from './ask-genie.growth-run-card';
import { SurfaceTitle } from '../components/Primitives';

/**
 * The run in flight, shown where the result will land (audit 2026-09-21
 * `genie-09`). A Growth Agent run is ONE blocking request and the server
 * emits no step events, so this is an honest indeterminate state: it names
 * the action and says what arrives, and never invents step progress. The
 * status text is static, so the live region announces it once.
 *
 * It scrolls itself into view (`block: 'nearest'`, so only when it is off
 * screen): a run started from a card further down the tab would otherwise
 * report its progress somewhere the user cannot see.
 */
export function GrowthAgentRunPending({ label }: { label: string }) {
  const ref = useRef<HTMLElement>(null);
  const reducedMotion = usePrefersReducedMotion();
  // Once per run card: a motion-preference change mid-run does not re-scroll.
  const revealedRef = useRef(false);
  useEffect(() => {
    const node = ref.current;
    if (revealedRef.current || !node || typeof node.scrollIntoView !== 'function') return;
    revealedRef.current = true;
    node.scrollIntoView({ block: 'nearest', behavior: reducedMotion ? 'auto' : 'smooth' });
  }, [reducedMotion]);
  return (
    <section
      ref={ref}
      className="growth-agent-run growth-agent-run--pending"
      role="status"
      aria-label="Growth Agent run in progress"
    >
      <div className="growth-agent-run__head">
        <div>
          <div className="eyebrow">In progress</div>
          <SurfaceTitle level={3}>{label}</SurfaceTitle>
        </div>
        <span className="typing-dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      </div>
      <p className="growth-agent-run__intent">
        Counting eligible borrowers and checking each review rule. The counts, the checks and the Lead Queue link
        appear here when the run finishes.
      </p>
    </section>
  );
}

interface GrowthAgentRunSlotProps {
  agent: GrowthAgentWorkspace;
  origin: GrowthAgentRunOrigin;
  onOpenRoute: (route: string) => void;
}

/**
 * Progress, error and result of the Growth Agent action started from this
 * tab. Feedback of an action started from the OTHER tab is not repeated here.
 */
export function GrowthAgentRunSlot({ agent, origin, onOpenRoute }: GrowthAgentRunSlotProps) {
  const here = agent.runOrigin === origin;
  return (
    <>
      {agent.activeRun?.origin === origin && <GrowthAgentRunPending label={agent.activeRun.label} />}
      {here && agent.growthAgentError && (
        <div className="status-callout status-callout--danger mt-3" role="alert">
          {agent.growthAgentError}
        </div>
      )}
      {here && agent.latestGrowthRun && (
        <GrowthAgentRunCard
          run={agent.latestGrowthRun}
          onOpenRoute={onOpenRoute}
          renderSourceAssetChip={renderSourceAssetChip}
        />
      )}
      {origin === 'workflows' && agent.composePlan && (
        <ComposePlanCard
          response={agent.composePlan}
          onOpenRoute={onOpenRoute}
          renderSourceAssetChip={renderSourceAssetChip}
        />
      )}
      {origin === 'monitors' && <GrowthAgentDraftPanel drafts={agent.latestGrowthDrafts} />}
    </>
  );
}
