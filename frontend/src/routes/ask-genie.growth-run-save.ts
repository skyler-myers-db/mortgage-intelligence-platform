import { useRef, useState } from 'react';
import { ApiError } from '../lib/api';
import { growthAgentRunsApi } from '../lib/apiClients/growthAgentRuns';
import type { GrowthAgentCadence, GrowthAgentMonitor, GrowthAgentRunResponse } from '../types';

/** The 404/409 copy: the run is gone, changed, failed or unaudited; nothing was saved. */
export const RUN_SAVE_REFUSED_MESSAGE = "This run can't be saved as shown. Run it again, then save.";

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
          const refused = error instanceof ApiError && (error.status === 404 || error.status === 409);
          const message = refused || !(error instanceof Error) || !error.message
            ? RUN_SAVE_REFUSED_MESSAGE
            : error.message;
          setState({ runId, pending: false, saved: null, errorMessage: message });
        },
      );
  }

  return { ...state, save };
}
