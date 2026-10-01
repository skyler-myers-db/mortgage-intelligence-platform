/**
 * Growth Agent run-ledger clients (audit 2026-09-21 `genie-09`): the caller's
 * recent reviewed-workflow runs and Save as watchlist for one executed run.
 *
 * Deliberately NOT spread into `api` (the rateScenario / genieJobs
 * precedent): `growthAgentApi` is in the initial closure through AppContext,
 * and these are used only by the lazy `/ask-genie` route. GET /runs is an
 * audit-free read; it is issued only while the Workflows tab is showing,
 * never on hover, prefetch or poll.
 */
import type { GrowthAgentMonitor } from '../../types';
import type { GrowthAgentRunSummary, GrowthAgentRunWatchlistRequest } from '../../types/growthAgent';
import { _newRequestId, getJson, postJson } from '../apiTransport';

export const GROWTH_AGENT_RUNS_PATH = '/api/growth-agent/runs';
/** The Workflows tab's run history length (one query key, so one cache entry). */
export const RECENT_RUNS_LIMIT = 10;

export const growthAgentRunsApi = {
  growthAgentRuns: (limit: number, signal?: AbortSignal) =>
    getJson<GrowthAgentRunSummary[]>(`${GROWTH_AGENT_RUNS_PATH}?limit=${encodeURIComponent(String(limit))}`, signal),

  /** Save exactly this run as a watchlist; the server re-plans nothing. */
  saveGrowthAgentRunWatchlist: (
    runId: string,
    payload: GrowthAgentRunWatchlistRequest,
    signal?: AbortSignal,
  ) => {
    const body: GrowthAgentRunWatchlistRequest = {
      ...payload,
      request_id: payload.request_id ?? _newRequestId(),
    };
    return postJson<GrowthAgentMonitor, GrowthAgentRunWatchlistRequest>(
      `${GROWTH_AGENT_RUNS_PATH}/${encodeURIComponent(runId)}/monitors`,
      body,
      signal,
    );
  },
};
