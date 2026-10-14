/**
 * The Follow-ups tray's reads and its one write (D-shell-deviations-a,
 * wow-power-3): GET /api/v1/sales/followups and POST
 * /api/v1/leads/{id}/snooze.
 *
 * Lazy: imported by the tray chunk only, never by the rail or spread into
 * `api` (api.ts sits in the initial closure). The list is audit-free and is
 * read only while the tray is open; the snooze writes one LEAD_SNOOZE row
 * (or one LEAD_SNOOZE_REFUSED row when the item may not be contacted).
 */
import type { ApiRequest, ApiResponse } from '../../types/api.gen';
import { getJson, postJson } from '../apiTransport';

export type FollowupList = ApiResponse<'FollowupList'>;
export type FollowupItem = ApiResponse<'FollowupItem'>;
export type LeadSnoozeRequest = ApiRequest<'LeadSnoozeRequest'>;
export type LeadSnoozeResponse = ApiResponse<'LeadSnoozeResponse'>;
export type SnoozeDays = LeadSnoozeRequest['snooze_days'];

export function fetchFollowupList(windowDays: number, signal?: AbortSignal): Promise<FollowupList> {
  return getJson<FollowupList>(`/api/v1/sales/followups?window_days=${windowDays}`, signal);
}

export function postLeadSnooze(borrowerId: string, body: LeadSnoozeRequest): Promise<LeadSnoozeResponse> {
  return postJson<LeadSnoozeResponse, LeadSnoozeRequest>(
    `/api/v1/leads/${encodeURIComponent(borrowerId)}/snooze`,
    body,
  );
}
