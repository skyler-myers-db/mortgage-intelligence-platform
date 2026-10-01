import { useRef, useState } from 'react';
import { ApiError } from '../lib/api';
import { growthAgentRunsApi } from '../lib/apiClients/growthAgentRuns';
import { CLIENT_FAILURE_MESSAGES } from '../lib/apiFailure';
import { clientFailureReason } from '../lib/apiTransport';
import type { GrowthAgentCadence, GrowthAgentMonitor, GrowthAgentRunResponse } from '../types';
import type { GrowthAgentWorkflowId } from '../types/growthAgent';

/** Any 4xx: the run is gone, changed, failed, unaudited or not savable; nothing was saved. */
export const RUN_SAVE_REFUSED_MESSAGE = "This run can't be saved as shown. Run it again, then save.";
/** A server or unknown failure. A retry is safe: saving the same run again keeps one watchlist. */
export const RUN_SAVE_FAILED_MESSAGE = "Couldn't save the watchlist right now. Try again shortly.";

/**
 * The reviewed workflows a run-ledger row can carry. The read-only live
 * analysis answer (`workflow.id` 'live_analysis', from
 * growth_agent_live_analysis.py) writes no `growth_agent_runs` row, so it is
 * deliberately absent. `satisfies` keeps this in step with the union.
 */
const REVIEWED_WORKFLOW_IDS: ReadonlySet<string> = new Set(Object.keys({
  daily_refi_brief: true,
  borrower_dossier_review: true,
  listing_watch: true,
  competitor_recapture_monitor: true,
  high_equity_heloc_watch: true,
  branch_capacity_review: true,
  source_freshness_sentinel: true,
  custom_segment_watch: true,
} satisfies Record<GrowthAgentWorkflowId, true>));

/**
 * Whether the card may offer Save as watchlist (audit 2026-09-21 `genie-09`
 * part 1): only a ledger-backed run of a reviewed workflow, with the 64-hex
 * result hash the save route binds and an audit row (the server refuses a run
 * without one). A live-analysis answer ("No state written") never qualifies.
 */
export function isSavableGrowthRun(
  run: Pick<GrowthAgentRunResponse, 'workflow' | 'tool_result_hash' | 'audit_event_id'>,
): boolean {
  return (
    REVIEWED_WORKFLOW_IDS.has(run.workflow.id)
    && /^[0-9a-f]{64}$/.test(run.tool_result_hash)
    && Boolean(run.audit_event_id)
  );
}

/**
 * Lender copy for a failed save. Never the error's own text: a 422 body names
 * schema fields and patterns. Session and connection failures keep the
 * transport's buyer copy; every other 4xx is the refused sentence.
 */
export function runSaveFailureMessage(error: unknown): string {
  const client = clientFailureReason(error);
  if (client) return CLIENT_FAILURE_MESSAGES[client];
  if (error instanceof ApiError && error.status !== null && error.status >= 400 && error.status < 500) {
    return RUN_SAVE_REFUSED_MESSAGE;
  }
  return RUN_SAVE_FAILED_MESSAGE;
}

interface RunSaveState {
  runId: string | null;
  pending: boolean;
  saved: GrowthAgentMonitor | null;
  errorMessage: string | null;
}

const IDLE: RunSaveState = { runId: null, pending: false, saved: null, errorMessage: null };

export interface GrowthRunSave extends RunSaveState {
  /** Save exactly this run (its id and result hash) as a watchlist. */
  save: (run: GrowthAgentRunResponse, cadence: GrowthAgentCadence) => void;
}

/**
 * Save as watchlist for the run the card shows (audit 2026-09-21 `genie-09`
 * part 1): `POST /api/growth-agent/runs/{run_id}/monitors` with the run's
 * `tool_result_hash`. The server saves the stored run's workflow, criteria
 * and route; nothing is planned or run again. Pessimistic: `onSaved` fires
 * only with the server's answer, and an answer to a superseded save is
 * dropped. A promise chain (no try/finally), so the React Compiler compiles it.
 */
export function useGrowthRunSave({
  onSaved,
}: {
  onSaved: (runId: string, monitor: GrowthAgentMonitor) => void;
}): GrowthRunSave {
  const [state, setState] = useState<RunSaveState>(IDLE);
  const latest = useRef(0);

  function save(run: GrowthAgentRunResponse, cadence: GrowthAgentCadence) {
    if (state.pending) return;
    latest.current += 1;
    const id = latest.current;
    const runId = run.run_id;
    setState({ runId, pending: true, saved: null, errorMessage: null });
    growthAgentRunsApi
      .saveGrowthAgentRunWatchlist(runId, { tool_result_hash: run.tool_result_hash, cadence })
      .then(
        (monitor) => {
          if (id !== latest.current) return;
          setState({ runId, pending: false, saved: monitor, errorMessage: null });
          onSaved(runId, monitor);
        },
        (error: unknown) => {
          if (id !== latest.current) return;
          setState({ runId, pending: false, saved: null, errorMessage: runSaveFailureMessage(error) });
        },
      );
  }

  return { ...state, save };
}
