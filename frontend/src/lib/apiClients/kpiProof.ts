/**
 * KPI reproduce SQL client (audit 2026-09-21 `flow-06` phase 2).
 *
 * Lazy-only, NOT spread into `api`: imported by the evidence drawer's lazy
 * body. One audit-free GET per KPI per document, issued only while that
 * KPI's drawer is open; the server emits the governed statement, nothing is
 * composed here.
 */
import type { KpiProofKey, KpiProofResponse } from '../apiTypes';
import { getJson } from '../apiTransport';

export const kpiProofApi = {
  kpiProof: (kpi: KpiProofKey, signal?: AbortSignal) =>
    getJson<KpiProofResponse>(`/api/kpi-proof?kpi=${encodeURIComponent(kpi)}`, signal),
};
