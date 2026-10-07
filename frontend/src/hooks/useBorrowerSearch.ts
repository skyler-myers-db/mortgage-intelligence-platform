import { useDeferredValue, useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import type { LeadSummary } from '../types';

/**
 * useBorrowerSearch: the ONE borrower / geography search behind the topbar
 * combobox and the ⌘K palette (2026-09-21 audit runtime-06, shell-07 item 1).
 * Before it each surface ran its own effect-driven search (180 ms and 160 ms
 * debounces, two AbortControllers, two result caches), so the same "Chicago"
 * was fetched twice and the two could disagree.
 *
 *   - Under MIN_BORROWER_SEARCH_CHARS trimmed characters nothing is requested:
 *     rows are [] and the status is 'idle'.
 *   - One debounce (BORROWER_SEARCH_DEBOUNCE_MS) through a timer-debounced
 *     term, rendered through useDeferredValue.
 *   - One cache entry per term (queryKeys.borrowerSearch): a term the other
 *     surface searched in the last 30 s is answered from the cache. The
 *     query function consumes the abort signal, so a superseded term aborts.
 *   - The previous term's rows stay on screen while the next term loads, but
 *     only while the term stays at two or more characters: a cleared search
 *     never flashes the rows of the search before it.
 *
 * GET /api/borrowers/search writes no audit row (it is not an audited read),
 * so the debounced read is safe; it is still never prefetched. The key sits
 * outside ['mip','borrower'] and the persistence allow-list, so a result is
 * never written to storage.
 */

export const MIN_BORROWER_SEARCH_CHARS = 2;
export const BORROWER_SEARCH_DEBOUNCE_MS = 160;
const BORROWER_SEARCH_STALE_MS = 30_000;
const BORROWER_SEARCH_GC_MS = 60_000;

export type BorrowerSearchStatus = 'idle' | 'loading' | 'empty' | 'error';

export interface BorrowerSearch {
  /** The debounced, trimmed term the query is keyed on ('' while inactive). */
  term: string;
  /** The term whose answer (rows, none, or a failure) is on screen ('' when none). */
  dataTerm: string;
  rows: readonly LeadSummary[];
  status: BorrowerSearchStatus;
  /** Search `term` now, skipping the debounce (a submit): the same key and function. */
  searchNow: (term: string) => Promise<LeadSummary[]>;
}

export interface BorrowerSearchOptions {
  /**
   * False (a closed palette): no request and the status reads idle; the
   * last answer's rows stay readable for the closing exit.
   */
  enabled?: boolean;
}

const NO_ROWS: readonly LeadSummary[] = [];

function searchQueryOptions(term: string) {
  return {
    queryKey: queryKeys.borrowerSearch(term),
    queryFn: ({ signal }: { signal: AbortSignal }) => api.borrowerSearch(term, signal),
    retry: false,
    staleTime: BORROWER_SEARCH_STALE_MS,
    gcTime: BORROWER_SEARCH_GC_MS,
  } as const;
}

export function useBorrowerSearch(query: string, { enabled = true }: BorrowerSearchOptions = {}): BorrowerSearch {
  const queryClient = useQueryClient();
  const trimmed = query.trim();
  const typed = trimmed.length >= MIN_BORROWER_SEARCH_CHARS;
  const active = enabled && typed;
  const [debounced, setDebounced] = useState('');
  // The last term whose own answer was on screen: the only rows a new term
  // may keep while it loads. Both reset at once when the text drops under
  // two characters.
  const [carryTerm, setCarryTerm] = useState('');
  if (!typed && debounced !== '') setDebounced('');
  if (!typed && carryTerm !== '') setCarryTerm('');

  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setTimeout(() => setDebounced(trimmed), BORROWER_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [active, trimmed]);

  const deferred = useDeferredValue(debounced);
  // A reset debounced term wins over a deferred value still catching up, so
  // a search typed again never resumes the term before the reset.
  const term = typed && debounced !== '' && deferred.length >= MIN_BORROWER_SEARCH_CHARS ? deferred : '';
  const search = useQuery({
    ...searchQueryOptions(term),
    // Disabled (a closed palette playing its exit): the last answer stays
    // readable from the cache, and nothing is requested.
    enabled: enabled && term !== '',
    placeholderData: (previous, previousQuery) => (
      carryTerm !== '' && previousQuery?.queryKey[2] === carryTerm ? previous : undefined
    ),
  });

  const answered = term !== '' && !search.isPlaceholderData && (search.isSuccess || search.isError);
  if (answered && carryTerm !== term) setCarryTerm(term);

  const searchNow = (next: string): Promise<LeadSummary[]> => {
    const nextTerm = next.trim();
    // The observer follows the submitted term at once, so it shares this
    // fetch (one request) and shows its outcome without the debounce.
    if (enabled && nextTerm.length >= MIN_BORROWER_SEARCH_CHARS) setDebounced(nextTerm);
    return queryClient.fetchQuery(searchQueryOptions(nextTerm));
  };

  // One result object (the shell's initial chunk carries this hook: one
  // memoized return, not one per branch). Under two characters `term` and
  // `carryTerm` are '' (reset above), so the idle defaults fall out.
  const rows = !typed || search.isError ? NO_ROWS : search.data ?? NO_ROWS;
  const settled = answered && term === trimmed;
  const status: BorrowerSearchStatus = !typed || !enabled
    ? 'idle'
    : !settled ? 'loading' : search.isError ? 'error' : rows.length > 0 ? 'idle' : 'empty';
  return { term, dataTerm: answered ? term : carryTerm, rows, status, searchNow };
}
