import type { QueryClient, QueryKey } from '@tanstack/react-query';

export const queryKeys = {
  all: ['mip'] as const,
  workspace: () => ['mip', 'workspace'] as const,
  footprint: () => ['mip', 'config', 'footprint'] as const,
  configOptions: () => ['mip', 'config', 'options'] as const,
  homePreview: () => ['mip', 'portfolio', 'preview', 'home'] as const,
  dataEstate: () => ['mip', 'data-estate'] as const,
  assetMetadata: (assetKey: string | null | undefined) => ['mip', 'asset', assetKey ?? ''] as const,
  analytics: (scope: string, criteria: readonly unknown[] = []) => ['mip', 'analytics', scope, ...criteria] as const,
  segments: (criteria: readonly unknown[]) => ['mip', 'segments', ...criteria] as const,
  leads: (criteria: readonly unknown[]) => ['mip', 'leads', ...criteria] as const,
  borrower: (borrowerId: string | null | undefined) => ['mip', 'borrower', borrowerId ?? ''] as const,
  borrowerProof: (borrowerId: string | null | undefined) => ['mip', 'borrower', borrowerId ?? '', 'proof'] as const,
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
  adminOperations: () => ['mip', 'admin', 'operations'] as const,
  adminCapabilities: () => ['mip', 'admin', 'capabilities'] as const,
  activationDestinations: () => ['mip', 'activation', 'destinations'] as const,
  activationSummary: () => ['mip', 'activation', 'summary'] as const,
  activationOutbox: (criteria: readonly unknown[]) => ['mip', 'activation', 'outbox', ...criteria] as const,
  auditEvents: (criteria: readonly unknown[]) => ['mip', 'audit', 'events', ...criteria] as const,
  auditRollups: (period: string, groupBy?: string | null) =>
    ['mip', 'audit', 'rollups', period, groupBy ?? 'event_type'] as const,
  genieStart: () => ['mip', 'genie', 'start'] as const,
  genieAnswer: (criteria: readonly unknown[]) => ['mip', 'genie', 'answer', ...criteria] as const,
  growthAgent: () => ['mip', 'growth-agent'] as const,
};

export function invalidateOperationalQueries(queryClient: QueryClient): Promise<void> {
  return Promise.all([
    queryClient.invalidateQueries({
      queryKey: ['mip', 'leads'] satisfies QueryKey,
      refetchType: 'none',
    }),
    queryClient.invalidateQueries({
      queryKey: ['mip', 'borrower'] satisfies QueryKey,
      refetchType: 'none',
    }),
    queryClient.invalidateQueries({
      queryKey: ['mip', 'sales'] satisfies QueryKey,
      refetchType: 'none',
    }),
    queryClient.invalidateQueries({
      queryKey: ['mip', 'audit'] satisfies QueryKey,
      refetchType: 'none',
    }),
    queryClient.invalidateQueries({
      queryKey: ['mip', 'portfolio'] satisfies QueryKey,
      refetchType: 'none',
    }),
    queryClient.invalidateQueries({
      queryKey: ['mip', 'segments'] satisfies QueryKey,
      refetchType: 'none',
    }),
    queryClient.invalidateQueries({
      queryKey: ['mip', 'analytics'] satisfies QueryKey,
      refetchType: 'none',
    }),
    queryClient.invalidateQueries({
      queryKey: ['mip', 'activation'] satisfies QueryKey,
      refetchType: 'none',
    }),
  ]).then(() => undefined);
}
