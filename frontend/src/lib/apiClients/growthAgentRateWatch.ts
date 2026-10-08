/**
 * "Watch this level" client (audit 2026-09-21 `wow-ai-2`):
 * `POST /api/growth-agent/rate-watch`.
 *
 * Lazy-only, deliberately NOT spread into `api` (the growthAgentRuns.ts
 * precedent): only RateWatchControl imports it, inside the Rate Lever's lazy
 * rateScenario chunk. The body is the step, the cadence and an idempotency id:
 * never a rate and never a name, because the server reads the watched level
 * from today's grid and names the watchlist itself. Nothing reads this route
 * on render, hover or prefetch.
 */
import type { GrowthAgentRateWatchRequest, GrowthAgentRateWatchResponse } from '../../types/growthAgent';
import { postJson } from '../apiTransport';

export const GROWTH_AGENT_RATE_WATCH_PATH = '/api/growth-agent/rate-watch';

export const growthAgentRateWatchApi = {
  saveRateWatch: (body: GrowthAgentRateWatchRequest, signal?: AbortSignal) =>
    postJson<GrowthAgentRateWatchResponse, GrowthAgentRateWatchRequest>(GROWTH_AGENT_RATE_WATCH_PATH, body, signal),
};
