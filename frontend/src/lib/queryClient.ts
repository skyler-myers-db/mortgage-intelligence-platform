import { QueryClient, type QueryKey } from '@tanstack/react-query';
import { isWarmingUpError } from './api';
import { planForReason } from './useWarmingUpRetry';

export const DEFAULT_QUERY_STALE_MS = 30_000;
export const DEFAULT_QUERY_GC_MS = 5 * 60_000;

export const queryKeys = {
  all: ['mip'] as const,
  workspace: () => ['mip', 'workspace'] as const,
  footprint: () => ['mip', 'config', 'footprint'] as const,
  configOptions: () => ['mip', 'config', 'options'] as const,
  homePreview: () => ['mip', 'portfolio', 'preview', 'home'] as const,
  dataEstate: () => ['mip', 'data-estate'] as const,
  segments: (criteria: readonly unknown[]) => ['mip', 'segments', ...criteria] as const,
  leads: (criteria: readonly unknown[]) => ['mip', 'leads', ...criteria] as const,
  borrower: (borrowerId: string | null | undefined) => ['mip', 'borrower', borrowerId ?? ''] as const,
  offerRecommendation: (borrowerId: string | null | undefined) =>
    ['mip', 'offer', 'recommendation', borrowerId ?? ''] as const,
  outreachDraft: (borrowerId: string | null | undefined, channel: string) =>
    ['mip', 'outreach', 'draft', borrowerId ?? '', channel] as const,
  salesTeam: () => ['mip', 'sales', 'team'] as const,
  salesOps: () => ['mip', 'sales', 'ops-snapshot'] as const,
  portfolioPreview: (criteria: readonly unknown[]) => ['mip', 'portfolio', 'preview', ...criteria] as const,
  campaigns: () => ['mip', 'campaigns'] as const,
  adminRules: () => ['mip', 'admin', 'rules'] as const,
  adminSources: () => ['mip', 'admin', 'sources'] as const,
  auditEvents: (criteria: readonly unknown[]) => ['mip', 'audit', 'events', ...criteria] as const,
  auditRollups: (period: string, groupBy?: string | null) =>
    ['mip', 'audit', 'rollups', period, groupBy ?? 'event_type'] as const,
  genieStart: () => ['mip', 'genie', 'start'] as const,
  genieAnswer: (criteria: readonly unknown[]) => ['mip', 'genie', 'answer', ...criteria] as const,
};

export function createMipQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: DEFAULT_QUERY_STALE_MS,
        gcTime: DEFAULT_QUERY_GC_MS,
        refetchOnWindowFocus: true,
        retry: (failureCount, error) => {
          if (!isWarmingUpError(error)) return false;
          const plan = planForReason(error.reason, error.dependency, {
            intervalMs: 5_000,
            maxAttempts: 6,
          });
          if (plan.stop) return false;
          return failureCount < Math.max(0, plan.maxAttempts - 1);
        },
        retryDelay: (_failureCount, error) => {
          if (!isWarmingUpError(error)) return 0;
          const plan = planForReason(error.reason, error.dependency, {
            intervalMs: 5_000,
            maxAttempts: 6,
          });
          return plan.intervalMs;
        },
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export function invalidateOperationalQueries(queryClient: QueryClient): Promise<void> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ['mip', 'leads'] satisfies QueryKey }),
    queryClient.invalidateQueries({ queryKey: ['mip', 'borrower'] satisfies QueryKey }),
    queryClient.invalidateQueries({ queryKey: ['mip', 'sales'] satisfies QueryKey }),
    queryClient.invalidateQueries({ queryKey: ['mip', 'audit'] satisfies QueryKey }),
    queryClient.invalidateQueries({ queryKey: ['mip', 'portfolio'] satisfies QueryKey }),
    queryClient.invalidateQueries({ queryKey: ['mip', 'segments'] satisfies QueryKey }),
  ]).then(() => undefined);
}
