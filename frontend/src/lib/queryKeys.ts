import type { QueryClient, QueryKey } from '@tanstack/react-query';

export const queryKeys = {
  all: ['mip'] as const,
  workspace: () => ['mip', 'workspace'] as const,
  footprint: () => ['mip', 'config', 'footprint'] as const,
  configOptions: () => ['mip', 'config', 'options'] as const,
  homePreview: () => ['mip', 'portfolio', 'preview', 'home'] as const,
  homeSummary: () => ['mip', 'home', 'summary'] as const,
  dataEstate: () => ['mip', 'data-estate'] as const,
  assetMetadata: (assetKey: string | null | undefined) => ['mip', 'asset', assetKey ?? ''] as const,
  lineageManifest: () => ['mip', 'lineage', 'manifest'] as const,
  analytics: (scope: string, criteria: readonly unknown[] = []) => ['mip', 'analytics', scope, ...criteria] as const,
  segments: (criteria: readonly unknown[]) => ['mip', 'segments', ...criteria] as const,
  leads: (criteria: readonly unknown[]) => ['mip', 'leads', ...criteria] as const,
  /**
   * The audit-free Lead Queue aggregates and the saved views (W5a). Outside
   * the ['mip','leads'] and ['mip','workspace'] prefixes on purpose: a leads
   * invalidation never refetches a closed menu's counts, and none of them is
   * ever persisted.
   */
  leadCount: (criteria: readonly unknown[]) => ['mip', 'lead-count', ...criteria] as const,
  leadFacets: (dimension: string, criteria: readonly unknown[]) => ['mip', 'lead-facets', dimension, ...criteria] as const,
  savedViews: () => ['mip', 'saved-views'] as const,
  borrower: (borrowerId: string | null | undefined) => ['mip', 'borrower', borrowerId ?? ''] as const,
  borrowerProof: (borrowerId: string | null | undefined) => ['mip', 'borrower', borrowerId ?? '', 'proof'] as const,
  borrowerLifecycle: (borrowerId: string | null | undefined) =>
    ['mip', 'borrower', borrowerId ?? '', 'lifecycle'] as const,
  /**
   * The Offer Orchestrator's one composite read: borrower, recommendation and
   * lifecycle for one open (routes/offer-orchestrator.queries.ts). Never under
   * ['mip','borrower'], so it never shares the Borrower 360 dossier entry (the
   * approval surface writes its own VIEW_BORROWER) and
   * invalidateOperationalQueries never touches it.
   */
  offerSnapshot: (
    borrowerId: string | null | undefined,
    binding: { campaign_id: string; variant_name: string } | null,
  ) => ['mip', 'offer', 'snapshot', borrowerId ?? '', binding?.campaign_id ?? '', binding?.variant_name ?? ''] as const,
  /** The governed outreach draft; carries every input of the POST (runtime-02 rule). */
  outreachDraft: (
    borrowerId: string | null | undefined,
    channel: string,
    binding: { campaign_id: string; variant_name: string } | null,
  ) => ['mip', 'outreach', 'draft', borrowerId ?? '', channel, binding?.campaign_id ?? '', binding?.variant_name ?? ''] as const,
  /** A county's ZIP rollups for one cohort (the Lead Queue's county scope chip). */
  geoCountyZipRollups: (countyFips: string, cohort: readonly unknown[]) =>
    ['mip', 'geo', 'county-zip-rollups', countyFips, ...cohort] as const,
  /**
   * The unfiltered /sales/team roster: the one cache entry every reader
   * shares (runtime-06, lib/salesRoster). Lead Queue and Sales ops narrow it
   * to loan officers, Offer to active loan officers and sales managers, each
   * through a `select`; never cache a filtered list under this key.
   */
  salesRoster: () => ['mip', 'sales', 'roster'] as const,
  salesOps: () => ['mip', 'sales', 'ops-snapshot'] as const,
  portfolioPreview: (criteria: readonly unknown[]) => ['mip', 'portfolio', 'preview', ...criteria] as const,
  campaigns: () => ['mip', 'campaigns'] as const,
  campaign: (campaignId: string | null | undefined) =>
    ['mip', 'campaigns', campaignId ?? ''] as const,
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
  auditReceipt: (auditEventId: string | null | undefined) =>
    ['mip', 'audit', 'receipt', auditEventId ?? ''] as const,
  genieStart: () => ['mip', 'genie', 'start'] as const,
  growthAgent: () => ['mip', 'growth-agent'] as const,
  growthAgentCapabilities: () => ['mip', 'growth-agent', 'capabilities'] as const,
  growthAgentRuns: (limit: number) => ['mip', 'growth-agent', 'runs', limit] as const,
  /** Home's watchlist briefings (wow-ai-4): audit-free, never persisted. */
  growthAgentWatchlistSummary: () => ['mip', 'growth-agent', 'monitors', 'summary'] as const,
  /** The Delta Explainer's read (wow-ai-3): audit-free; not persisted (only ['mip','home','summary'] is). */
  homeSummaryAttribution: (measure: string, baseline: string) =>
    ['mip', 'home', 'summary', 'attribution', measure, baseline] as const,
  /**
   * The evidence drawer's every-user freshness read (critic-03): audit-free,
   * never persisted, and never a prefix-child of assetMetadata ['mip','asset',key].
   */
  assetFreshness: (assetKey: string | null | undefined) => ['mip', 'asset-freshness', assetKey ?? ''] as const,
  /** One KPI's server-emitted reproduce SQL (flow-06): fixed text, never persisted. */
  kpiProof: (kpi: string | null | undefined) => ['mip', 'kpi-proof', kpi ?? ''] as const,
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
