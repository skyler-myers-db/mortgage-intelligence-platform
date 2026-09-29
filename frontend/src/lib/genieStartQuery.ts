import { queryOptions } from '@tanstack/react-query';
import type { GenieStartResult } from '../types';
import { api } from './api';
import { queryKeys } from './queryKeys';

/**
 * The ONE `/api/genie/start` read both Genie surfaces observe (audit
 * 2026-09-21 `runtime-06`, Genie slice): the starters, the trusted assets and
 * a bootstrap conversation id. The floating panel used to fetch it in an
 * effect of its own on every mount, so opening the panel after /ask-genie had
 * already read it sent a second request; now the two share this cache entry.
 *
 * Audit-exempt (it writes no VIEW_* row), and read only where a Genie surface
 * is mounted: the panel mounts on its first open, so a never-opened panel
 * sends nothing. Five minutes fresh, never refetched on focus. The shell's
 * `queryClient.clear()` drops it at an actor switch.
 */
export function genieStartQueryOptions() {
  return queryOptions<GenieStartResult>({
    queryKey: queryKeys.genieStart(),
    queryFn: ({ signal }) => api.genieStart(signal),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}
