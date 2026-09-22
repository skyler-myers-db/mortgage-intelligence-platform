/**
 * Growth co-pilot endpoint clients: home summary, capability probe, monitor
 * reads and runs, and the reviewed workflow executors.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type {
  GrowthAgentDueMonitorRunRequest,
  GrowthAgentDueMonitorRunResponse,
  GrowthAgentHomeResponse,
  GrowthAgentMonitor,
  GrowthAgentMonitorDraftRequest,
  GrowthAgentNotificationDraft,
  ComposePlanRequest,
  ComposePlanResponse,
  GrowthAgentCustomRunRequest,
  GrowthAgentPromptRunRequest,
  GrowthAgentRunRequest,
  GrowthAgentRunResponse,
  GrowthAgentWorkflowId,
} from '../../types';
import { _newRequestId, getJson, postJson } from '../apiTransport';

export const growthAgentApi = {
  growthAgent: (signal?: AbortSignal) =>
    getJson<GrowthAgentHomeResponse>('/api/growth-agent', signal),

  growthAgentCapabilities: (signal?: AbortSignal) =>
    getJson<GrowthAgentHomeResponse>('/api/growth-agent?live_capabilities=1', signal),

  growthAgentMonitors: (signal?: AbortSignal) =>
    getJson<GrowthAgentMonitor[]>('/api/growth-agent/monitors', signal),

  runDueGrowthAgentMonitors: (
    payload: GrowthAgentDueMonitorRunRequest = {},
    signal?: AbortSignal,
  ) => {
    const body: GrowthAgentDueMonitorRunRequest = {
      channels: ['slack', 'teams'],
      ...payload,
      request_id: payload.request_id ?? _newRequestId(),
    };
    return postJson<GrowthAgentDueMonitorRunResponse, GrowthAgentDueMonitorRunRequest>(
      '/api/growth-agent/monitors/run-due',
      body,
      signal,
    );
  },

  createGrowthAgentMonitorNotificationDrafts: (
    monitorId: string,
    payload: GrowthAgentMonitorDraftRequest = {},
    signal?: AbortSignal,
  ) => {
    const body: GrowthAgentMonitorDraftRequest = {
      channels: ['slack', 'teams'],
      ...payload,
      request_id: payload.request_id ?? _newRequestId(),
    };
    return postJson<GrowthAgentNotificationDraft[], GrowthAgentMonitorDraftRequest>(
      `/api/growth-agent/monitors/${encodeURIComponent(monitorId)}/notification-drafts`,
      body,
      signal,
    );
  },

  rerunGrowthAgentMonitor: (
    monitorId: string,
    payload: GrowthAgentRunRequest = {},
    signal?: AbortSignal,
  ) => {
    const body: GrowthAgentRunRequest = {
      ...payload,
      request_id: payload.request_id ?? _newRequestId(),
    };
    return postJson<GrowthAgentRunResponse, GrowthAgentRunRequest>(
      `/api/growth-agent/monitors/${encodeURIComponent(monitorId)}/run`,
      body,
      signal,
    );
  },

  runGrowthAgentWorkflow: (
    workflowId: GrowthAgentWorkflowId,
    payload: GrowthAgentRunRequest = {},
    signal?: AbortSignal,
  ) => {
    const body: GrowthAgentRunRequest = {
      ...payload,
      request_id: payload.request_id ?? _newRequestId(),
    };
    return postJson<GrowthAgentRunResponse, GrowthAgentRunRequest>(
      `/api/growth-agent/workflows/${workflowId}/run`,
      body,
      signal,
    );
  },

  runCustomGrowthAgentWorkflow: (
    payload: GrowthAgentCustomRunRequest,
    signal?: AbortSignal,
  ) => {
    const body: GrowthAgentCustomRunRequest = {
      ...payload,
      request_id: payload.request_id ?? _newRequestId(),
    };
    return postJson<GrowthAgentRunResponse, GrowthAgentCustomRunRequest>(
      '/api/growth-agent/custom/run',
      body,
      signal,
    );
  },

  runMortgageGrowthAgent: (
    payload: GrowthAgentPromptRunRequest,
    signal?: AbortSignal,
  ) => {
    const body: GrowthAgentPromptRunRequest = {
      ...payload,
      request_id: payload.request_id ?? _newRequestId(),
    };
    return postJson<GrowthAgentRunResponse, GrowthAgentPromptRunRequest>(
      '/api/growth-agent/agent/run',
      body,
      signal,
    );
  },

  composeMortgageGrowthAgentPlan: (
    payload: ComposePlanRequest,
    signal?: AbortSignal,
  ) => {
    const body: ComposePlanRequest = {
      ...payload,
      request_id: payload.request_id ?? _newRequestId(),
    };
    return postJson<ComposePlanResponse, ComposePlanRequest>(
      '/api/growth-agent/agent/compose',
      body,
      signal,
    );
  },
};
