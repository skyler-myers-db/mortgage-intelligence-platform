/**
 * Composed-plan progress client (audit 2026-09-21 `genie-09` part 4):
 * `GET /api/growth-agent/agent/plan/progress?request_id=`.
 *
 * Lazy-only, deliberately NOT spread into `api`: only the /ask-genie route's
 * compose-plan execute hook imports it. The read is audit-free and returns the
 * caller's own request's step; it is polled about once a second only while
 * that request's execute is pending, never on hover, prefetch or render.
 */
import type { GrowthAgentPlanProgressResponse } from '../../types/growthAgent';
import { getJson } from '../apiTransport';

export const GROWTH_AGENT_PLAN_PROGRESS_PATH = '/api/growth-agent/agent/plan/progress';

export const growthAgentProgressApi = {
  planProgress: (requestId: string, signal?: AbortSignal) =>
    getJson<GrowthAgentPlanProgressResponse>(
      `${GROWTH_AGENT_PLAN_PROGRESS_PATH}?request_id=${encodeURIComponent(requestId)}`,
      signal,
    ),
};
