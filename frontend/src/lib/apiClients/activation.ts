/**
 * Activation endpoint clients: destination roster, outbox reads, and the
 * staging call that queues an approved audience for a destination.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type {
  ActivationDestination,
  ActivationOutboxItem,
  ActivationStageResponse,
  ActivationSummary,
} from '../../types';
import { _newRequestId, getJson, postJson } from '../apiTransport';

export const activationApi = {
  activationSummary: (signal?: AbortSignal) =>
    getJson<ActivationSummary>('/api/activation/summary', signal),

  activationDestinations: (signal?: AbortSignal) =>
    getJson<ActivationDestination[]>('/api/activation/destinations', signal),

  activationOutbox: (
    opts: { borrowerId?: string | null; destinationKey?: string | null; limit?: number } = {},
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams();
    if (opts.borrowerId) params.set('borrower_id', opts.borrowerId);
    if (opts.destinationKey) params.set('destination_key', opts.destinationKey);
    if (opts.limit) params.set('limit', String(opts.limit));
    const qs = params.toString();
    return getJson<ActivationOutboxItem[]>(
      qs ? `/api/activation/outbox?${qs}` : '/api/activation/outbox',
      signal,
    );
  },

  stageActivation: (
    payload: {
      borrower_id: string;
      destination_key: string;
      offer_code?: string | null;
      channel?: 'email' | 'sms' | 'direct_mail';
      campaign_id?: string | null;
      approval_id: string;
      request_id?: string;
    },
    signal?: AbortSignal,
  ) =>
    postJson<ActivationStageResponse, {
      borrower_id: string;
      destination_key: string;
      offer_code?: string | null;
      channel: 'email' | 'sms' | 'direct_mail';
      campaign_id?: string | null;
      approval_id: string;
      request_id: string;
    }>(
      '/api/activation/stage',
      {
        ...payload,
        channel: payload.channel ?? 'email',
        campaign_id: payload.campaign_id ?? null,
        approval_id: payload.approval_id,
        request_id: payload.request_id ?? _newRequestId(),
      },
      signal,
    ),
};
