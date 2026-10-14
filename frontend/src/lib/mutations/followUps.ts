/**
 * The Follow-ups tray's Snooze write (wow-power-3 phase 2). PESSIMISTIC: no
 * row moves until the server answers 2xx; the caller keeps the row, with the
 * chosen button loading, for the whole request.
 *
 * Same write posture as mutations/sales.ts: networkMode 'always', retry
 * false, one request_id per intent (borrower + days) reused on every retry of
 * that intent and forgotten after a success. The body is exactly
 * {snooze_days, request_id}: never text.
 *
 *   2xx           invalidate ['mip','followups'] (the tray list and the rail
 *                 dot re-read) and raise a success toast linked to the
 *                 LEAD_SNOOZE row;
 *   409 not_contactable / no_open_follow_up
 *                 an error toast in plain words, and the list re-reads (the
 *                 row moves under "No longer contactable" or leaves);
 *   429           the shell toast platform's Retry with the Retry-After wait;
 *   anything else the describeApiError title and body.
 */
import { useMutation, type QueryClient } from '@tanstack/react-query';
import { postLeadSnooze, type LeadSnoozeResponse, type SnoozeDays } from '../apiClients/followUps';
import { ApiError } from '../apiTransport';
import { describeApiError } from '../describeApiError';
import { formatDate } from '../time';
import { toast } from '../toast';
import { intentFingerprint, useIntentRequestIds, type IntentRequestIds } from './requestIds';

export const followUpsMutationKeys = {
  snooze: ['mip', 'followups', 'snooze'] as const,
};

const FOLLOW_UPS_KEY = ['mip', 'followups'] as const;

export const SNOOZE_NOT_CONTACTABLE = 'This lead can no longer be contacted. It stays under No longer contactable.';
export const SNOOZE_NOT_OPEN = 'This follow-up is no longer open.';

export interface SnoozeVariables {
  borrowerId: string;
  snoozeDays: SnoozeDays;
  requestId: string;
}

export function snoozedToastTitle(result: LeadSnoozeResponse): string {
  return `Snoozed until ${formatDate(result.wake_at)}${result.wake_clamped ? ' (contact frequency cap)' : ''}`;
}

interface SnoozeDeps {
  mutateAsync: (variables: SnoozeVariables) => Promise<LeadSnoozeResponse>;
  requestIds: IntentRequestIds;
  refetch: () => Promise<void>;
}

/** One snooze intent; module-level so the 429 Retry can re-run it (same request_id). */
async function runSnooze(deps: SnoozeDeps, borrowerId: string, snoozeDays: SnoozeDays): Promise<LeadSnoozeResponse | null> {
  const fingerprint = intentFingerprint('snooze', borrowerId, snoozeDays);
  const requestId = deps.requestIds.idFor(fingerprint);
  try {
    const result = await deps.mutateAsync({ borrowerId, snoozeDays, requestId });
    deps.requestIds.settle(fingerprint);
    void deps.refetch();
    toast.success(snoozedToastTitle(result), { auditEventId: result.audit_event_id });
    return result;
  } catch (error) {
    if (error instanceof ApiError && error.status === 409
      && (error.message === 'not_contactable' || error.message === 'no_open_follow_up')) {
      deps.requestIds.settle(fingerprint);
      toast.error(error.message === 'not_contactable' ? SNOOZE_NOT_CONTACTABLE : SNOOZE_NOT_OPEN);
      void deps.refetch();
      return null;
    }
    const description = describeApiError(error, { subject: 'Snooze' });
    if (description.kind === 'aborted') return null;
    if (description.kind === 'rate_limited') {
      toast.info(description.title, {
        detail: description.body,
        action: { label: 'Retry', onAction: () => void runSnooze(deps, borrowerId, snoozeDays) },
        retryAfterMs: description.retryAfterMs,
      });
      return null;
    }
    toast.error(description.title, { detail: description.body });
    return null;
  }
}

export function useSnoozeFollowUp(queryClient: QueryClient) {
  const requestIds = useIntentRequestIds();
  const mutation = useMutation<LeadSnoozeResponse, Error, SnoozeVariables>(
    {
      mutationKey: followUpsMutationKeys.snooze,
      mutationFn: ({ borrowerId, snoozeDays, requestId }) =>
        postLeadSnooze(borrowerId, { snooze_days: snoozeDays, request_id: requestId }),
      networkMode: 'always',
      retry: false,
    },
    queryClient,
  );
  const deps: SnoozeDeps = {
    mutateAsync: mutation.mutateAsync,
    requestIds,
    refetch: () => queryClient.invalidateQueries({ queryKey: FOLLOW_UPS_KEY }),
  };

  return {
    snooze: (borrowerId: string, snoozeDays: SnoozeDays) => runSnooze(deps, borrowerId, snoozeDays),
    /** The intent in flight (the row keeps its loading button), or null. */
    pending: mutation.isPending ? (mutation.variables ?? null) : null,
  };
}
