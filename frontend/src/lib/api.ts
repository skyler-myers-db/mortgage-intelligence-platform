import type { LeadQueryOptions } from './apiTypes';
import { analyticsApi } from './apiClients/analytics';
import { portfolioApi, campaignApi } from './apiClients/portfolio';
import { geoApi } from './apiClients/geo';
import { leadsPageApi, borrowerApi } from './apiClients/leads';
import { outreachApi } from './apiClients/outreach';
import { salesApi } from './apiClients/sales';
import { activationApi } from './apiClients/activation';
import { genieApi } from './apiClients/genie';
import { growthAgentApi } from './apiClients/growthAgent';
import { auditApi } from './apiClients/audit';
import { workspaceApi } from './apiClients/workspace';
import { adminApi } from './apiClients/admin';

/**
 * API client — calls the FastAPI backend and surfaces errors honestly.
 *
 * Per CLAUDE.md: the app runs on real Unity Catalog data or it fails
 * visibly. This module does NOT silently fall back to mock fixtures on
 * error. Every failure throws a structured `ApiError` that callers
 * render as an explicit empty/error state (e.g. "Couldn't load
 * segments"). Transient 503s are handled by the built-in retry loop
 * that mirrors the backend's Resilient wrapper cadence; the
 * <DegradedBanner> polls /api/health in parallel and surfaces the
 * "backend is warming up" messaging while retries are in flight.
 *
 * The only method that tolerates failure is `health()`: an unreachable
 * /api/health returns `{ status: 'unreachable', mode: 'unknown',
 * dependencies: {} }`. That's honest status, not synthetic data.
 *
 * Cancellation (round-2 hole-finder #10/#11, 2026-04-23): every method
 * accepts an optional `AbortSignal`. Callers pass a controller's
 * signal in the effect body and call `controller.abort()` from the
 * cleanup function so an unmount / rapid-refilter actually cancels
 * the in-flight request. An aborted fetch throws a DOMException with
 * name === 'AbortError'; we re-throw it as an ApiError with
 * `retryable: false` so call sites can `if (err.name === 'AbortError')`
 * cheaply, or inspect `.aborted` on the ApiError.
 */

// Response/query contracts and HTTP transport moved to sibling modules when
// this file was split for the file-size gate. `./api` stays the ONLY import
// path for consumers, so everything they used to get from here is re-exported
// verbatim below; nothing outside `src/lib/` imports the sibling modules.
export type {
  HealthPayload,
  ApproveResult,
  RejectResult,
  OutreachDraftResult,
  GenieResult,
  GenieFeedbackResult,
  GenieSubmitResult,
  GenieLiveProgress,
  AuditEventRow,
  AuditEventPage,
  ActorAuditEventSummary,
  ActorAuditEventPage,
  SegmentFilterMode,
  LeadFunnelStage,
  LeadQueryOptions,
  GrowthAgentCohortProof,
  GrowthAgentCohortVerification,
  AnalyticsQueryOptions,
  AssignmentResponse,
  DispositionResponse,
  LeadsPageResult,
  GeoQueryCriteria,
  ZipRollupKey,
  GeoOverlayLevel,
  GeoAssignmentOverlayUnit,
  GeoAssignmentOverlayResponse,
} from './apiTypes';
export type { ApiErrorReason, ApiValidationIssue, Retryable503Parsed } from './apiTransport';
export {
  ApiError,
  isWarmingUpError,
  dependencyLabel,
  isAbortError,
  growthAgentCohortFingerprint,
  _parseRetryableBody,
  _parseHttpErrorBody,
} from './apiTransport';

/**
 * The api client. Each route group is a const object in `./apiClients/`,
 * spread here in the original member order so `Object.keys(api)` is
 * unchanged. `leads` stays in this file because it calls `api.leadsPage`
 * through the exported object, which tests spy on.
 */
export const api = {
  ...analyticsApi,
  ...portfolioApi,
  ...geoApi,
  ...leadsPageApi,

  leads: (
    segment?: string,
    signal?: AbortSignal,
    geo?: { state?: string; zip?: string; county?: string; counties?: string[]; states?: string[]; zips?: string[]; cities?: string[]; borrowerIds?: string[] },
    opts: LeadQueryOptions = {},
  ) => api.leadsPage(segment, signal, geo, opts).then((page) => page.leads),
  ...borrowerApi,
  ...outreachApi,
  ...salesApi,
  ...activationApi,
  ...campaignApi,
  ...genieApi,
  ...growthAgentApi,
  ...auditApi,
  ...workspaceApi,
  ...adminApi,
};
