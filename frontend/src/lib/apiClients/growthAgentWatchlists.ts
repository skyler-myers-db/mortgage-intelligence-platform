/**
 * Watchlist briefings client (audit 2026-09-21 `wow-ai-4`): the caller's
 * saved watchlists with their run-over-run change and the scheduler's real
 * state, for Home's WatchlistBriefings card.
 *
 * Lazy-only, deliberately NOT spread into `api` (the growthAgentRuns.ts
 * precedent): `growthAgentApi` is initial JS through AppContext, and this is
 * imported only by the lazy WatchlistBriefings chunk. GET /monitors/summary
 * is audit-free and starts no run; Home issues it once on load, never on
 * hover, prefetch or poll.
 */
import type { GrowthAgentWatchlistSummaryResponse } from '../../types/growthAgent';
import { getJson } from '../apiTransport';

export const GROWTH_AGENT_WATCHLIST_SUMMARY_PATH = '/api/growth-agent/monitors/summary';

export const growthAgentWatchlistsApi = {
  watchlistSummary: (signal?: AbortSignal) =>
    getJson<GrowthAgentWatchlistSummaryResponse>('/api/growth-agent/monitors/summary', signal),
};
