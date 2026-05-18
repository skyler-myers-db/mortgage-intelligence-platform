import { queryOptions, useQuery } from '@tanstack/react-query';
import { api } from './api';
import { queryKeys } from './queryKeys';

export const CONFIG_OPTIONS_STALE_MS = 60_000;

export function configOptionsQueryOptions() {
  return queryOptions({
    queryKey: queryKeys.configOptions(),
    queryFn: ({ signal }) => api.configOptions(signal),
    staleTime: CONFIG_OPTIONS_STALE_MS,
    retry: false,
  });
}

export function useConfigOptionsQuery() {
  return useQuery(configOptionsQueryOptions());
}
