import { useContext } from 'react';
import { QueryClient, QueryClientContext } from '@tanstack/react-query';
import { usePendingDecisions, type OutreachDecision } from '../../lib/mutations/outreach';

/**
 * Is an approve or reject for this borrower on the wire right now, from ANY
 * surface (2026-09-21 audit stack-09 item 5)? The ApprovalBanner used to know
 * only its caller's isSubmitting, so a decision started in the Lead Queue's
 * row (or a second tab of the same mutation cache) left the Offer's banner
 * looking idle. Read from the shared MutationCache (usePendingDecisions,
 * imported, never edited here): pessimistic, it never says 'approved', only
 * that a decision is being recorded.
 *
 * The banner also renders without a QueryClientProvider (static markup, the
 * Lead Queue preview's tests): the client then falls back to a module-level
 * inert QueryClient whose cache holds nothing, so nothing reads as pending.
 *
 * Lazy-only: this module (and lib/mutations/outreach's useMutationState) is
 * reached only through ApprovalBanner, which ships with the Offer route and
 * the LeadTable chunk, never the initial closure.
 */
const INERT_CLIENT = new QueryClient();

export function usePendingApprovalBanner(borrowerId?: string | null): OutreachDecision | null {
  const client = useContext(QueryClientContext) ?? INERT_CLIENT;
  const pending = usePendingDecisions(client);
  return borrowerId ? pending.get(borrowerId) ?? null : null;
}
