/**
 * Administration's Field performance read (D-platform-process-d2 step 12):
 * p75 Core Web Vitals per route template from the browser RUM day
 * aggregates. Admin-only and audit-free.
 *
 * Imported directly by the lazy Field performance panel, never spread into
 * `api`: api.ts sits in the initial closure, and this client is needed only
 * once Administration renders the panel.
 */
import type { ApiResponse } from '../../types/api.gen';
import { getJson } from '../apiTransport';

export type FieldPerformanceResponse = ApiResponse<'FieldPerformanceResponse'>;
export type FieldPerformanceCell = ApiResponse<'FieldPerformanceCell'>;
export type FieldPerformanceDays = FieldPerformanceResponse['days'];

export function fetchFieldPerformance(days: FieldPerformanceDays, signal?: AbortSignal): Promise<FieldPerformanceResponse> {
  return getJson<FieldPerformanceResponse>(`/api/admin/field-performance?days=${days}`, signal);
}
