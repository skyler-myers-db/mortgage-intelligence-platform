/**
 * The Offer Orchestrator's reads on the query layer (2026-09-21 audit
 * runtime-06 (a)(f), delivery-08 L slice, states-03 item 2, runtime-03).
 *
 * AUDIT PARITY is the contract (ruled in wave 5, D-audit-reads-b: the Offer
 * keeps its own audited read, there is no VIEW_OFFER, and RECOMMEND_OFFER is
 * the approval-surface open record; docs/security-and-compliance.md "Read-audit
 * semantics by surface"). Every open of this approval surface writes
 * VIEW_BORROWER (GET /borrowers/{id}), RECOMMEND_OFFER (POST /offers/recommend)
 * and DRAFT_OUTREACH (POST /outreach/draft), exactly as the effect this module
 * replaced did (it re-read all four on every open, re-open, channel switch,
 * Regenerate, reset and Retry; its module cache only hydrated the screen).
 * So:
 *
 *   - The snapshot (borrower + recommendation + lifecycle) is ONE composite
 *     query under queryKeys.offerSnapshot, never the Borrower 360 key: the
 *     approval surface records what the approver saw with its own read.
 *     staleTime 0 + refetchOnMount: a re-open re-reads (and a hydrated
 *     snapshot is display-only until it does); gcTime 5 min is the old
 *     hydrate-then-refetch cache.
 *   - The draft is its own query with gcTime 0 and staleTime Infinity: one
 *     POST per open, never a background refetch.
 *   - Neither refetches on window focus or reconnect, and neither is ever
 *     prefetched, loaded by a route loader or read on hover.
 *   - networkMode 'always': offline fails at once, exactly as the effect did,
 *     instead of pausing and firing a read later that nobody asked for.
 *   - SINGLE OBSERVER: only the route calls useOfferSnapshot / useOfferDraft.
 *     Children get the data as props. A second observer of a stale query
 *     refetches it on mount, and that refetch writes audit rows.
 *
 * A retryable 503 left in a query's error keeps it an active errored query,
 * so HealthProvider's refetchRecoveredQueries re-runs it once when that
 * dependency recovers (states-03); nothing else re-fires it.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, isWarmingUpError } from '../lib/api';
import type { OutreachDraftResult } from '../lib/apiTypes';
import { clientFailureReason } from '../lib/apiTransport';
import { queryKeys } from '../lib/queryKeys';
import { planForReason } from '../lib/retryPlan';
import type { WarmingUpState } from '../lib/useWarmingUpRetry';
import type { Borrower360, BorrowerLifecycle, OfferRecommendation } from '../types';
import type { OutreachChannel } from './offer-orchestrator.constants';
import { offerSnapshotMatches } from './offer-orchestrator.snapshot';

/** A verified `?campaign_id=&variant_name=` binding (offer-orchestrator.route-ui). */
export interface OfferQueryBinding {
  campaign_id: string;
  variant_name: string;
}

export interface OfferSnapshot {
  borrower: Borrower360;
  recommendation: OfferRecommendation;
  lifecycle: BorrowerLifecycle | null;
}

export const OFFER_SNAPSHOT_MISMATCH_MESSAGE =
  'The borrower and offer snapshots changed while loading. Retry after the data refresh completes.';

/** The borrower and offer reads cited different gold refreshes: retried fresh, then surfaced. */
export class OfferSnapshotMismatchError extends Error {
  constructor() {
    super(OFFER_SNAPSHOT_MISMATCH_MESSAGE);
    this.name = 'OfferSnapshotMismatchError';
  }
}

/** Keeps the old hydrate-then-refetch window (BORROWER_CACHE's 5-minute TTL). */
export const OFFER_SNAPSHOT_GC_MS = 5 * 60_000;
/** A gold refresh landed between the reads: retry every 750 ms, 6 attempts in all. */
export const SNAPSHOT_MISMATCH_RETRY_MS = 750;
const SNAPSHOT_MISMATCH_MAX_ATTEMPTS = 6;
const WARMING_DEFAULTS = { intervalMs: 5_000, maxAttempts: 6 } as const;

/** useWarmingUpRetry's formula: the reason's plan, never past its attempt budget. */
function warmingRetry(failureCount: number, error: unknown): boolean {
  if (!isWarmingUpError(error)) return false;
  const plan = planForReason(error.reason, error.dependency, WARMING_DEFAULTS);
  if (plan.stop) return false;
  return failureCount < Math.max(0, plan.maxAttempts - 1);
}

function warmingDelay(error: unknown): number {
  if (!isWarmingUpError(error)) return 0;
  return planForReason(error.reason, error.dependency, WARMING_DEFAULTS).intervalMs;
}

/** The WarmingUpBlock state for a warming failure, or null. */
function warmingState(error: unknown, failureCount: number): WarmingUpState | null {
  if (!isWarmingUpError(error)) return null;
  const plan = planForReason(error.reason, error.dependency, WARMING_DEFAULTS);
  if (plan.stop) return null;
  return {
    dependency: error.dependency,
    label: plan.label,
    attempt: Math.min(failureCount + 1, Math.max(1, plan.maxAttempts)),
    maxAttempts: plan.maxAttempts,
    correlationId: error.correlationId,
    intervalMs: plan.intervalMs,
  };
}

export function offerSnapshotRetry(failureCount: number, error: unknown): boolean {
  // An ended session, no network, an unreachable app: the shell owns those.
  if (clientFailureReason(error)) return false;
  if (error instanceof OfferSnapshotMismatchError) return failureCount < SNAPSHOT_MISMATCH_MAX_ATTEMPTS - 1;
  return warmingRetry(failureCount, error);
}

export function offerSnapshotRetryDelay(_failureCount: number, error: unknown): number {
  if (error instanceof OfferSnapshotMismatchError) return SNAPSHOT_MISMATCH_RETRY_MS;
  return warmingDelay(error);
}

async function readOfferSnapshot(id: string, signal: AbortSignal, fresh: boolean): Promise<OfferSnapshot> {
  const [borrower, recommendation, lifecycle] = await Promise.all([
    api.borrower(id, signal, fresh),
    api.recommendOffer(id, signal),
    api.borrowerLifecycle(id, signal).catch(() => null),
  ]);
  if (!offerSnapshotMatches(borrower, recommendation)) throw new OfferSnapshotMismatchError();
  return { borrower, recommendation, lifecycle };
}

function loadErrorCopy(error: unknown): string | null {
  if (!error) return null;
  return error instanceof Error ? `Couldn't load borrower or offer: ${error.message}` : "Couldn't load borrower or offer.";
}

export interface OfferSnapshotRead {
  data: OfferSnapshot | null;
  /** The read of THIS open is in flight: a hydrated snapshot is display-only. */
  reading: boolean;
  warmingUp: WarmingUpState | null;
  /** A refresh landed between the reads and the retry is running. */
  reconciling: boolean;
  /** "Couldn't load borrower or offer: …" once the read has failed for good. */
  loadError: string | null;
  notFound: boolean;
  /** Re-read the snapshot now (cancels a read in flight, as the effect's re-run did). */
  refetch: () => void;
}

/** The route's one observer of the snapshot. */
export function useOfferSnapshot(id: string | undefined, binding: OfferQueryBinding | null): OfferSnapshotRead {
  const queryKey = queryKeys.offerSnapshot(id, binding);
  const query = useQuery<OfferSnapshot, unknown>({
    queryKey,
    // `fresh` on every retry within one fetch cycle (the effect's attempt > 1):
    // the backend bypasses its dossier cache while a refresh is reconciled.
    queryFn: ({ signal, client, queryKey: key }) =>
      readOfferSnapshot(id ?? '', signal, (client.getQueryState(key)?.fetchFailureCount ?? 0) > 0),
    enabled: Boolean(id),
    staleTime: 0,
    gcTime: OFFER_SNAPSHOT_GC_MS,
    refetchOnMount: true,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    networkMode: 'always',
    retry: offerSnapshotRetry,
    retryDelay: offerSnapshotRetryDelay,
  });
  const fetching = query.isFetching;
  const failure = query.failureReason;
  const error = fetching ? null : query.error;
  const { refetch } = query;
  return {
    data: query.data ?? null,
    reading: fetching,
    warmingUp: fetching ? warmingState(failure, query.failureCount) : null,
    reconciling: fetching && failure instanceof OfferSnapshotMismatchError,
    loadError: loadErrorCopy(error),
    notFound: error instanceof ApiError && error.status === 404,
    refetch: () => {
      void refetch();
    },
  };
}

export const EMPTY_DRAFT_MESSAGE =
  'Offer draft endpoint returned an empty draft. Approval is disabled until an audited draft loads.';
export const CAMPAIGN_INCOMPLETE_DRAFT_MESSAGE =
  'Campaign handoff is incomplete. Reopen the saved campaign and select a variant.';

export interface OfferDraftProof {
  generationId: string;
  responseHash: string;
  sourceRefreshedAt: string;
}

export interface OfferDraftView {
  pending: boolean;
  loaded: boolean;
  warming: WarmingUpState | null;
  error: string | null;
  body: string;
  subject: string;
  proof: OfferDraftProof | null;
  disclosureVersion: string | null;
  disclosureState: string | null;
  generatorLabel: string | null;
  generationMode: OutreachDraftResult['generation_mode'] | null;
  strategy: string | null;
  evidence: string[];
  evidenceAssets: string[];
  /** Explicit re-read (Regenerate, Retry draft, reset, load-error Retry): one new POST. */
  reset: () => void;
}

async function readOfferDraft(
  id: string,
  channel: OutreachChannel,
  binding: OfferQueryBinding | null,
  signal: AbortSignal,
): Promise<OutreachDraftResult> {
  const draft = binding
    ? await api.draftOutreach(id, channel, signal, binding)
    : await api.draftOutreach(id, channel, signal);
  if (
    binding
    && (
      draft.campaign_id !== binding.campaign_id
      || draft.variant_name !== binding.variant_name
      || draft.channel !== channel
    )
  ) {
    throw new Error('Campaign variant proof is stale. Reopen the saved campaign before approval.');
  }
  return draft;
}

function draftErrorCopy(error: unknown): string {
  return error instanceof Error ? `Offer draft unavailable: ${error.message}` : 'Offer draft unavailable.';
}

const NO_DRAFT = {
  body: '',
  subject: '',
  proof: null,
  disclosureVersion: null,
  disclosureState: null,
  generatorLabel: null,
  generationMode: null,
  strategy: null,
  evidence: [] as string[],
  evidenceAssets: [] as string[],
};

/** The route's one observer of the governed draft for this open. */
export function useOfferDraft(
  id: string | undefined,
  channel: OutreachChannel,
  binding: OfferQueryBinding | null,
  bindingError: boolean,
): OfferDraftView {
  const queryClient = useQueryClient();
  const queryKey = queryKeys.outreachDraft(id, channel, binding);
  const query = useQuery<OutreachDraftResult, unknown>({
    queryKey,
    queryFn: ({ signal }) => readOfferDraft(id ?? '', channel, binding, signal),
    enabled: Boolean(id) && !bindingError,
    // One POST per open (DRAFT_OUTREACH): dropped as soon as the page lets
    // go of it, never refetched behind the reviewer's back.
    staleTime: Infinity,
    gcTime: 0,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    networkMode: 'always',
    retry: warmingRetry,
    retryDelay: (_failureCount, error) => warmingDelay(error),
  });
  const reset = () => {
    void queryClient.resetQueries({ queryKey, exact: true });
  };
  if (bindingError) {
    return { ...NO_DRAFT, pending: false, loaded: false, warming: null, error: CAMPAIGN_INCOMPLETE_DRAFT_MESSAGE, reset };
  }
  const draft = query.data;
  if (draft === undefined) {
    const failed = query.status === 'error';
    return {
      ...NO_DRAFT,
      pending: !failed,
      loaded: false,
      warming: query.isFetching ? warmingState(query.failureReason, query.failureCount) : null,
      error: failed ? draftErrorCopy(query.error) : null,
      reset,
    };
  }
  if (!draft.body || draft.body.trim().length === 0) {
    return { ...NO_DRAFT, pending: false, loaded: false, warming: null, error: EMPTY_DRAFT_MESSAGE, reset };
  }
  return {
    pending: false,
    loaded: true,
    warming: null,
    error: null,
    body: draft.body,
    subject: draft.subject ?? '',
    proof: {
      generationId: draft.generation_id,
      responseHash: draft.response_hash,
      sourceRefreshedAt: draft.source_refreshed_at,
    },
    disclosureVersion: draft.disclosure_version,
    disclosureState: draft.disclosure_state,
    generatorLabel: draft.generator_label,
    generationMode: draft.generation_mode,
    strategy: draft.strategy_summary,
    evidence: draft.evidence_summary,
    evidenceAssets: draft.evidence_assets,
    reset,
  };
}
