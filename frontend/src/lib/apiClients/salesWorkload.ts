/**
 * The Sales ops per-officer workload read (audit 2026-09-21 critic-10 item
 * 6c, W5e w5-analytics): GET /api/sales/workload, manager / admin only,
 * Lakebase only, AUDIT-FREE. Imported by the lazy SalesWorkloadTable alone,
 * never spread into `api`, so neither the route nor the initial closure
 * carries it.
 */
import type { ApiResponse } from '../../types/api.gen';
import { getJson } from '../apiTransport';

export type SalesWorkloadResponse = ApiResponse<'SalesWorkloadResponse'>;
export type SalesWorkloadRow = ApiResponse<'SalesWorkloadRow'>;

export function fetchSalesWorkload(fromDate: string, toDate: string, signal?: AbortSignal): Promise<SalesWorkloadResponse> {
  const params = new URLSearchParams();
  params.set('from', fromDate);
  params.set('to', toDate);
  return getJson<SalesWorkloadResponse>(`/api/sales/workload?${params.toString()}`, signal);
}
