import { type ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type {
  CampaignListResponse,
  CampaignRecommendationResponse,
  CampaignSummary,
  PortfolioPreview,
  CampaignPerformanceFunnelResponse,
} from '../types';
import { PageShell } from '../components/layout/PageShell';
import { CampaignPrefillBanner } from '../components/mortgage/CampaignPrefillBanner';
import { KpiCard } from '../components/mortgage/KpiCard';
import { Button } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { useApp } from '../components/AppContext';
import { FilterSelect } from '../components/ui/FilterSelect';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { parseCampaignPrefill } from '../lib/campaignPrefill';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { parseBackendTimestamp } from '../lib/time';
import { useFootprint } from '../components/FootprintProvider';
import { queryKeys } from '../lib/queryKeys';
import { RoiProjector, StateMultiSelect } from './portfolio-builder.components';
import { CampaignSetupPanel } from './portfolio-builder.campaign-setup';
import {
  savedCampaignLeadQueueUrl,
  savedCampaignVariants,
} from './portfolio-builder.saved-campaigns';
import {
  BASE_DEFAULT_FILTERS,
  DEFAULT_CAMPAIGN_SETUP,
  NON_GEO_FILTER_GROUPS,
  URL_FILTER_KEYS,
  buildCampaignConfig,
  buildGeoOptions,
  buildLeadQueueUrlFromFilters,
  buildPreviewCriteria,
  buildSegmentIntelligenceUrlFromFilters,
  buildUrlFromFilters,
  campaignCopyValidationError,
  campaignCriteriaSummary,
  groupSavedCampaigns,
  dayZeroSafe,
  defaultGeographyForOptions,
  formatDelta,
  isDayZero,
  parseFiltersFromUrl,
  parseStateCodesFromUrl,
  type CampaignSetupState,
} from './portfolio-builder.logic';
import { HIGH_OPPORTUNITY_KPI_LABEL } from '../lib/opportunityScore';

/**
 * Portfolio Builder — prototype `.surface` + `.filter-row` composition.
 * Filter dropdowns drive a population estimate; KPI grid reads from
 * /api/portfolio/preview. "Generate Approval Required Outreach" is the
 * primary forward motion into segment intelligence.
 */

/** Compact "Mon D" date for a saved-campaign timestamp (wire-safe parse). */
function formatSavedCampaignDate(iso: string | null): string | null {
  const parsed = parseBackendTimestamp(iso);
  return parsed ? parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : null;
}

function isoDateDaysAgo(days: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

export default function PortfolioBuilder() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { setDrawer, canAccessAdmin } = useApp();
  const footprint = useFootprint();
  // S9 geo → campaign handoff: a `prefill_source=geo-drilldown` link seeds a
  // draft context banner. The state predicate itself applies through the
  // existing `states=` param via parseStateCodesFromUrl below — no second
  // application path here.
  const campaignPrefill = useMemo(() => parseCampaignPrefill(searchParams), [searchParams]);
  const configOptionsQuery = useConfigOptionsQuery();
  const targetLenderOptions = useMemo(() => {
    const values = configOptionsQuery.data?.target_lender_refs?.filter(Boolean);
    return values && values.length > 0 ? values : ['All'];
  }, [configOptionsQuery.data?.target_lender_refs]);
  const targetLenderStatus = configOptionsQuery.isError
    ? 'unavailable'
    : configOptionsQuery.data?.target_lender_refs_status ?? 'loading';
  // Build the GEO dropdown from the tenant footprint. Memoised so the
  // FilterSelect doesn't get a fresh options array on every render (it
  // would be identity-stable for same-footprint re-renders).
  const geoOptions = useMemo(
    () => buildGeoOptions(
      footprint.ready && !footprint.usingFallback ? footprint.states : [],
    ),
    [footprint.ready, footprint.states, footprint.usingFallback],
  );
  const defaultFilters = useMemo(
    () => ({ ...BASE_DEFAULT_FILTERS }),
    [],
  );
  const geoOptionsKey = useMemo(() => geoOptions.join('\u0000'), [geoOptions]);
  const defaultGeo = useMemo(() => defaultGeographyForOptions(geoOptions), [geoOptions]);
  const filterGroups = useMemo(
    () => [
      ...NON_GEO_FILTER_GROUPS.slice(0, 3),
      { label: 'TARGET LIEN HOLDER', key: 'target_lender_ref', options: targetLenderOptions },
      ...NON_GEO_FILTER_GROUPS.slice(3),
    ],
    [targetLenderOptions],
  );
  // Initialize from URL so deep-links and browser back/forward work. On
  // mount we also trigger a build, so a shared link reproduces the
  // exact KPI grid the sender saw. Round-2 hole-finder #16, 2026-04-23.
  const [filters, setFilters] = useState<Record<string, string>>(() =>
    parseFiltersFromUrl(searchParams, defaultFilters, targetLenderOptions),
  );
  const [stateCodes, setStateCodes] = useState<string[]>(() =>
    parseStateCodesFromUrl(searchParams, footprint.states),
  );
  // The "committed" filter payload drives the useWarmingUpRetry hook.
  // `filters` tracks the dropdown state, `committedFilters` is what the
  // KPI grid reflects — only updated via onRunBuild or URL navigation.
  // This preserves the prototype UX: filter changes don't refetch; the
  // "Run build" button is the explicit commit point.
  const [committedFilters, setCommittedFilters] = useState<Record<string, string>>(
    () => parseFiltersFromUrl(searchParams, defaultFilters, targetLenderOptions),
  );
  const [committedStateCodes, setCommittedStateCodes] = useState<string[]>(() =>
    parseStateCodesFromUrl(searchParams, footprint.states),
  );
  const [copyHint, setCopyHint] = useState<'idle' | 'copied' | 'failed'>('idle');
  const [saveHint, setSaveHint] = useState<'idle' | 'saved' | 'failed'>('idle');
  // Inline naming form for "Save build". Re-audit #3 P1 (2026-06-12): the
  // previous native prompt() dialog is SYNCHRONOUS — it blocks the renderer
  // main thread (a hard freeze under any CDP/Playwright session, and an
  // un-themeable OS dialog for humans). Enterprise polish + e2e testability
  // both want this in-page async form instead. A source pin in
  // portfolio-builder.save.test.tsx keeps the blocking API out for good.
  const [savePanelOpen, setSavePanelOpen] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [saveValidationError, setSaveValidationError] = useState<string | null>(null);
  const [campaignSetup, setCampaignSetup] = useState<CampaignSetupState>(DEFAULT_CAMPAIGN_SETUP);
  const [selectedCampaignVariants, setSelectedCampaignVariants] = useState<Record<string, string>>({});
  const {
    data: campaignsData,
    isPending: campaignsLoading,
    isError: campaignsIsError,
    refetch: refetchCampaigns,
  } = useQuery<CampaignListResponse>({
    queryKey: queryKeys.campaigns(),
    queryFn: ({ signal }) => api.campaigns(signal),
    retry: false,
  });
  const campaigns: CampaignSummary[] = campaignsData?.campaigns ?? [];
  const campaignsError = campaignsIsError ? 'Saved campaigns unavailable' : null;

  // Cold-start warming-up loop. Re-runs whenever committedFilters
  // changes (via Run build or URL navigation). 6 retries / 5s apart =
  // 30s of auto-retry before surfacing the red error path.
  const committedKey = useMemo(
    () => JSON.stringify({ filters: committedFilters, stateCodes: committedStateCodes }),
    [committedFilters, committedStateCodes],
  );
  const {
    data: preview,
    warmingUp,
    error,
    manualRetry: retryBuild,
    isFetching: previewFetching,
  } = useWarmingUpRetry<PortfolioPreview>(
    (signal) => api.portfolioPreview(buildPreviewCriteria(committedFilters, committedStateCodes), signal),
    [committedKey],
    { queryKey: queryKeys.portfolioPreview([committedKey]), keepPreviousData: true },
  );
  const building = preview === null && warmingUp === null && error === null;
  // Re-audit #3 P1 (2026-06-12): `building` is false during a background
  // refetch of an UNCHANGED build key (stale preview still rendered), so
  // "Run build → Save build within ~1s" found Save enabled mid-build.
  // Everything that must not race an in-flight build gates on this.
  const buildInFlight = building || previewFetching;
  const previewError = error
    ? error instanceof Error
      ? `Couldn't load portfolio preview: ${error.message}`
      : "Couldn't load portfolio preview."
    : null;
  // Both SQL endpoints include start and end, so 89 days back plus today is
  // exactly 90 calendar days.
  const observedFrom = useMemo(() => isoDateDaysAgo(89), []);
  const observedTo = useMemo(() => isoDateDaysAgo(0), []);
  const performanceQuery = useQuery<CampaignPerformanceFunnelResponse>({
    queryKey: ['portfolio-campaign-performance', observedFrom, observedTo],
    queryFn: ({ signal }) => api.salesCampaignPerformance(observedFrom, observedTo, signal),
    retry: false,
  });
  const recommendationQuery = useQuery<CampaignRecommendationResponse>({
    queryKey: ['portfolio-campaign-recommendation', committedKey],
    queryFn: ({ signal }) => api.campaignRecommendation(
      buildPreviewCriteria(committedFilters, committedStateCodes),
      signal,
    ),
    enabled: Boolean(preview && preview.marketable_population > 0),
    retry: false,
  });

  const setFilter = (key: string) => (next: string) => setFilters((f) => ({ ...f, [key]: next }));
  const setCampaignField = (key: Exclude<
    keyof CampaignSetupState,
    | 'marketHouseholdTogether'
    | 'generationMode'
    | 'generatorLabel'
    | 'provenanceTokenA'
    | 'provenanceTokenB'
  >) => (
    event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => setCampaignSetup((current) => ({
    ...current,
    [key]: event.target.value,
    ...(['subjectA', 'subjectB', 'bodyA', 'bodyB'].includes(key)
      ? {
          generationMode: 'operator' as const,
          generatorLabel: 'Operator edited',
          provenanceTokenA: null,
          provenanceTokenB: null,
        }
      : {}),
  }));
  const toggleHouseholdDedup = useCallback(() => {
    setCampaignSetup((current) => ({
      ...current,
      marketHouseholdTogether: !current.marketHouseholdTogether,
    }));
  }, []);
  const applyRecommendation = useCallback(() => {
    const recommendation = recommendationQuery.data;
    if (!recommendation || recommendation.variants.length !== 2) return;
    const [variantA, variantB] = recommendation.variants;
    setCampaignSetup((current) => ({
      ...current,
      subjectA: variantA.subject,
      subjectB: variantB.subject,
      bodyA: variantA.body,
      bodyB: variantB.body,
      holdoutPct: String(recommendation.holdout_pct),
      generationMode: recommendation.generation_mode,
      generatorLabel: recommendation.generator_label,
      provenanceTokenA: variantA.provenance_token,
      provenanceTokenB: variantB.provenance_token,
    }));
  }, [recommendationQuery.data]);
  const buildDirty = useMemo(
    () =>
      JSON.stringify({ filters, stateCodes }) !==
      JSON.stringify({ filters: committedFilters, stateCodes: committedStateCodes }),
    [committedFilters, committedStateCodes, filters, stateCodes],
  );

  /**
   * Commit the current filter state: push to URL, then refetch. The
   * URL is the source of truth for a shareable build so we update it
   * on the explicit "Run build" click rather than on every dropdown
   * change (which would pollute browser history with every keystroke).
   */
  const onRunBuild = useCallback(() => {
    setSearchParams(buildUrlFromFilters(filters, defaultFilters, stateCodes, targetLenderOptions), { replace: false });
    setCommittedFilters(filters);
    setCommittedStateCodes(stateCodes);
    // A new run invalidates any in-progress naming: the criteria the save
    // would capture are about to change under the operator's cursor.
    setSavePanelOpen(false);
  }, [defaultFilters, filters, setSearchParams, stateCodes, targetLenderOptions]);

  /**
   * Copy the current URL to the clipboard. Falls back to a failed
   * hint if the Clipboard API is unavailable (Safari private mode,
   * old browsers). The URL already reflects the last committed build
   * because onRunBuild wrote to it.
   */
  const onCopyLink = useCallback(async () => {
    if (buildDirty) return;
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopyHint('copied');
    } catch {
      setCopyHint('failed');
    }
  }, [buildDirty]);

  const saveRequestRef = useRef<{ fingerprint: string; requestId: string } | null>(null);
  const onOpenSavePanel = useCallback(() => {
    if (buildDirty || buildInFlight) return;
    saveRequestRef.current = null;
    setSaveName(`Portfolio build ${new Date().toLocaleString()}`);
    setSaveValidationError(null);
    setSavePanelOpen(true);
  }, [buildDirty, buildInFlight]);

  const [saving, setSaving] = useState(false);
  const onConfirmSave = useCallback(async () => {
    const name = saveName.trim();
    if (!name || saving) return;
    const copyError = campaignCopyValidationError(campaignSetup);
    if (copyError) {
      setSaveValidationError(copyError);
      setSaveHint('failed');
      return;
    }
    setSaveValidationError(null);
    // Re-audit #4 (2026-06-12): the panel used to close BEFORE the await,
    // so a failed save silently discarded the operator's typed name. Keep
    // the panel (and the name) until the save actually succeeds; on failure
    // the form stays open with the typed name and a "Save failed" hint so
    // the operator can retry without re-typing.
    setSaving(true);
    try {
      const criteria = buildPreviewCriteria(committedFilters, committedStateCodes);
      const campaignConfig = buildCampaignConfig(campaignSetup);
      const fingerprint = JSON.stringify({ name, criteria, campaignConfig });
      if (saveRequestRef.current?.fingerprint !== fingerprint) {
        saveRequestRef.current = {
          fingerprint,
          requestId: crypto.randomUUID(),
        };
      }
      await api.portfolioCreate(
        name,
        criteria,
        { ...campaignConfig, request_id: saveRequestRef.current.requestId },
      );
      await refetchCampaigns();
      saveRequestRef.current = null;
      setSavePanelOpen(false);
      setSaveHint('saved');
    } catch {
      setSaveHint('failed');
    } finally {
      setSaving(false);
    }
  }, [campaignSetup, committedFilters, committedStateCodes, refetchCampaigns, saveName, saving]);

  useEffect(() => {
    if (copyHint === 'idle') return;
    const t = window.setTimeout(() => setCopyHint('idle'), 1800);
    return () => window.clearTimeout(t);
  }, [copyHint]);

  useEffect(() => {
    if (saveHint === 'idle') return;
    const t = window.setTimeout(() => setSaveHint('idle'), 2200);
    return () => window.clearTimeout(t);
  }, [saveHint]);

  // When the URL changes (browser back/forward), reconcile local state
  // and refetch so the KPI grid reflects the navigation. We only
  // refetch if the URL-derived filters actually differ from local
  // state — otherwise setState from onRunBuild would cause an
  // unnecessary second fetch.
  const urlFilters = useMemo(
    () => parseFiltersFromUrl(searchParams, defaultFilters, targetLenderOptions),
    [defaultFilters, searchParams, targetLenderOptions],
  );
  const urlStateCodes = useMemo(
    () => parseStateCodesFromUrl(searchParams, footprint.states),
    [footprint.states, searchParams],
  );
  useEffect(() => {
    const differs = URL_FILTER_KEYS.some((k) => urlFilters[k] !== filters[k]);
    const stateDiffers = urlStateCodes.join(',') !== stateCodes.join(',');
    if (differs || stateDiffers) {
      setFilters(urlFilters);
      setCommittedFilters(urlFilters);
      setStateCodes(urlStateCodes);
      setCommittedStateCodes(urlStateCodes);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlFilters, urlStateCodes]);

  useEffect(() => {
    const allowed = new Set(footprint.states.map((state) => state.state_code));
    const sanitize = (codes: string[]) => {
      const next = codes.filter((code) => allowed.has(code));
      const collapsed =
        footprint.states.length > 0 && next.length === footprint.states.length ? [] : next;
      // Identity-preserving no-op guard (re-audit #3, 2026-06-12): always
      // returning a fresh array re-renders on every effect pass, which
      // turns into an unbounded setState loop if a provider hands back
      // unstable identities (a test mock did exactly that). Same values
      // in -> same reference out.
      return collapsed.length === codes.length && collapsed.every((c, i) => c === codes[i])
        ? codes
        : collapsed;
    };
    setStateCodes(sanitize);
    setCommittedStateCodes(sanitize);
  }, [footprint.states, geoOptionsKey]);

  const leadQueueUrl = useMemo(() => {
    return buildLeadQueueUrlFromFilters(committedFilters, committedStateCodes, targetLenderOptions);
  }, [committedFilters, committedStateCodes, targetLenderOptions]);
  const segmentIntelligenceUrl = useMemo(() => {
    return buildSegmentIntelligenceUrlFromFilters(committedFilters, targetLenderOptions);
  }, [committedFilters, targetLenderOptions]);

  return (
    <PageShell
      eyebrow="Portfolio Builder"
      title="Build a borrower population"
      lede="Apply geography, lien, relationship, Owner Link, purchase intent, product, and equity filters. KPIs show size, score, and conversion."
    >
      <CampaignPrefillBanner prefill={campaignPrefill.prefill} error={campaignPrefill.error} />
      <div className="surface">
        <div className="surface__hdr surface__hdr--split">
          <div className="surface__hdr-main">
            <div className="surface__icon">
              <Icon name="target" size={14} />
            </div>
            <div>
              <div className="h-4">Filters</div>
              <div className="muted fs-12">
                Filter the population, run the build, review KPIs.
              </div>
            </div>
          </div>
        </div>
        <div className="surface__body">
          <div className="filter-row">
            <StateMultiSelect
              label="GEO"
              allLabel={defaultGeo}
              states={footprint.ready && !footprint.usingFallback ? footprint.states : []}
              value={stateCodes}
              onChange={setStateCodes}
            />
            {filterGroups.map((g) => (
              <FilterSelect
                key={g.key}
                label={g.label}
                value={filters[g.key]}
                options={g.options}
                onChange={setFilter(g.key)}
              />
            ))}
            <div className="filter-row__spacer" />
            <Button
              variant="ghost"
              size="default"
              icon="link"
              onClick={() => void onCopyLink()}
              disabled={buildDirty}
              aria-label="Copy shareable URL for the current build"
              data-testid="portfolio-copy-link"
            >
              {copyHint === 'copied'
                ? 'Link copied'
                : copyHint === 'failed'
                ? 'Copy failed'
                : buildDirty
                ? 'Run before sharing'
                : 'Share this build'}
            </Button>
            <Button
              variant="ghost"
              size="default"
              icon="doc"
              onClick={onOpenSavePanel}
              disabled={buildDirty || buildInFlight}
              aria-label="Save current portfolio build"
              aria-expanded={savePanelOpen}
              data-testid="portfolio-save-build"
            >
              {saveHint === 'saved'
                ? 'Build saved'
                : saveHint === 'failed'
                ? 'Save failed'
                : buildDirty
                ? 'Run before saving'
                : buildInFlight
                ? 'Build running…'
                : 'Save build'}
            </Button>
            <Button
              variant="primary"
              icon="play"
              onClick={onRunBuild}
              disabled={buildInFlight}
              aria-busy={buildInFlight}
            >
              {buildInFlight ? 'Running…' : 'Run build'}
            </Button>
          </div>
          {savePanelOpen && (
            <form
              className="save-build-form"
              onSubmit={(e) => {
                e.preventDefault();
                void onConfirmSave();
              }}
            >
              <label className="save-build-form__label" htmlFor="portfolio-save-name">
                Build name
              </label>
              <input
                id="portfolio-save-name"
                className="form-input save-build-form__input"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                maxLength={120}
                autoFocus
                data-testid="portfolio-save-name"
              />
              <Button
                variant="primary"
                size="sm"
                type="submit"
                icon="check"
                disabled={saveName.trim().length === 0 || saving}
                aria-busy={saving}
                data-testid="portfolio-save-confirm"
              >
                {saving ? 'Saving…' : 'Save'}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                type="button"
                onClick={() => setSavePanelOpen(false)}
                disabled={saving}
                data-testid="portfolio-save-cancel"
              >
                Cancel
              </Button>
              {saveHint === 'failed' && (
                <span className="save-build-form__error" role="alert">
                  {saveValidationError ?? 'Save failed — your name is kept; try again.'}
                </span>
              )}
            </form>
          )}
          {targetLenderStatus !== 'live' && (
            <div className="filter-row__hint muted">
              Target lien holder options are limited until live borrower lender aliases finish refreshing.
            </div>
          )}

          {warmingUp && preview === null && (
            <div className="mt-4">
              <WarmingUpBlock
                state={warmingUp}
                title="Portfolio preview loading"
                compact
              />
            </div>
          )}
          {warmingUp && preview !== null && (
            <div className="mt-4">
              <span className="chip chip--neutral chip--compact stable-status-chip">
                {warmingUp.label} ({warmingUp.attempt}/{warmingUp.maxAttempts})
              </span>
            </div>
          )}
          {previewError && !warmingUp && (
            <div
              role="alert"
              className="status-callout status-callout--danger mt-4"
            >
              <span>{previewError}</span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={retryBuild}
                aria-label="Retry portfolio preview"
              >
                Retry
              </button>
            </div>
          )}

          {/* Day-0 empty-state banner (hole-finder round 2 #13, 2026-04-23).
              On a fresh customer workspace the funnel snapshot table is
              empty and borrower_360 has no rows — the preview returns 0s
              with a null timestamp, which would otherwise render as a
              plausible-but-misleading all-zero KPI row.
              R5-20: keyed off ``isDayZero`` so the server flag wins when
              present and the two-field inference is only the fallback. */}
          {isDayZero(preview) && (
            <div
              role="status"
              className="status-callout status-callout--day-zero mt-4"
            >
              <strong>First data refresh pending.</strong>{' '}
              Unity Catalog gold tables are empty.{' '}
              {canAccessAdmin ? (
                <>
                  Open <Link to="/admin-config#data-operations">Admin Data Operations</Link>
                  {' '}and run the governed scoring refresh after the source-feature refresh completes.
                </>
              ) : (
                <>Contact an administrator to run the governed scoring refresh after the source-feature refresh completes.</>
              )}
            </div>
          )}
          {!isDayZero(preview) && preview?.trend_note && (
            <div
              role="status"
              className="status-callout status-callout--info mt-4"
            >
              {preview.trend_note}
            </div>
          )}

          <div className="kpi-row kpi-row--spaced">
            <KpiCard
              label="Marketable population"
              valueAnimated={dayZeroSafe(preview, preview?.marketable_population)}
              trend={preview?.trends?.marketable_population?.series}
              delta={formatDelta(preview?.trends?.marketable_population)}
              deltaDir={preview?.trends?.marketable_population?.direction}
              trendNote={preview?.trends?.marketable_population?.note}
              loading={building}
              source={DRAWER_SOURCES.population}
            />
            <KpiCard
              label="Avg. borrower score"
              valueAnimated={dayZeroSafe(preview, preview?.avg_score)}
              trend={preview?.trends?.avg_score?.series}
              delta={formatDelta(preview?.trends?.avg_score)}
              deltaDir={preview?.trends?.avg_score?.direction}
              trendNote={preview?.trends?.avg_score?.note}
              loading={building}
              source={DRAWER_SOURCES.leadScore}
            />
            <KpiCard
              label={HIGH_OPPORTUNITY_KPI_LABEL}
              valueAnimated={dayZeroSafe(preview, preview?.top_tier_opportunities)}
              trend={preview?.trends?.top_tier_opportunities?.series}
              delta={formatDelta(preview?.trends?.top_tier_opportunities)}
              deltaDir={preview?.trends?.top_tier_opportunities?.direction}
              trendNote={preview?.trends?.top_tier_opportunities?.note}
              loading={building}
              source={DRAWER_SOURCES.leadScore}
            />
            <KpiCard
              label="Primary offer paths"
              valueAnimated={dayZeroSafe(preview, preview?.offers_recommended)}
              trend={preview?.trends?.offers_recommended?.series}
              delta={formatDelta(preview?.trends?.offers_recommended)}
              deltaDir={preview?.trends?.offers_recommended?.direction}
              trendNote={preview?.trends?.offers_recommended?.note}
              loading={building}
              source={DRAWER_SOURCES.nbo}
            />
          </div>
        </div>
      </div>

      {/* Projected economics (re-audit #4 Buyer-Wow #7): only once a real
          build with refinance-economics leads exists — never on day-zero or an
          empty cohort, where a dollar headline would be misleading. */}
      {!isDayZero(preview) && (preview?.high_intent_leads ?? 0) > 0 && (
        <RoiProjector
          preview={preview!}
          performance={performanceQuery.data}
          performanceStatus={
            performanceQuery.isPending
              ? 'loading'
              : performanceQuery.isError
                ? 'unavailable'
                : 'available'
          }
        />
      )}

      <CampaignSetupPanel
        setup={campaignSetup}
        recommendation={recommendationQuery.data}
        recommendationPending={recommendationQuery.isPending}
        recommendationError={recommendationQuery.isError}
        recommendationFetching={recommendationQuery.isFetching}
        canRecommend={Boolean(preview && preview.marketable_population > 0)}
        canAccessAdmin={canAccessAdmin}
        onFieldChange={setCampaignField}
        onNumericFieldCommit={(key, value) => setCampaignSetup((current) => ({
          ...current,
          [key]: value,
        }))}
        onToggleHouseholdDedup={toggleHouseholdDedup}
        onRegenerate={() => void recommendationQuery.refetch()}
        onApply={applyRecommendation}
      />

      <div className="surface mt-4">
        <div className="surface__hdr surface__hdr--split">
          <div className="surface__hdr-main">
            <div className="surface__icon">
              <Icon name="doc" size={14} />
            </div>
            <div>
              <div className="h-4">Saved campaigns</div>
              <div className="muted fs-12">Drafts and review status for portfolio builds.</div>
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            icon="tweak"
            onClick={() => void refetchCampaigns()}
            aria-label="Refresh saved campaigns"
          >
            Refresh
          </Button>
        </div>
        <div className="surface__body">
          {campaignsLoading ? (
            <div className="muted fs-12">Loading campaigns…</div>
          ) : campaignsError ? (
            <div className="status-callout status-callout--danger">{campaignsError}</div>
          ) : campaigns.length === 0 ? (
            <div className="muted fs-12">No saved campaigns.</div>
          ) : (
            <div className="saved-workspace">
              <div className="saved-workspace__summary">
                <span>{campaigns.length.toLocaleString()} saved</span>
                <span>eligible-only policy required before approval</span>
              </div>
              {groupSavedCampaigns(campaigns).slice(0, 8).map((row) => {
                const campaign = row.campaign;
                const variants = savedCampaignVariants(campaign);
                const selectedVariantName = selectedCampaignVariants[campaign.campaign_id]
                  ?? variants[0]?.variantName
                  ?? '';
                const selectedVariant = variants.find(
                  (variant) => variant.variantName === selectedVariantName,
                );
                const householdEnabled = campaign.household_dedup?.enabled === true;
                const suppressedCount = campaign.household_summary?.suppressed_co_owner_count ?? 0;
                const grouped = row.draftCount > 1;
                const latestSaved = grouped ? formatSavedCampaignDate(row.latestAt) : null;
                return (
                  <div key={campaign.campaign_id} className="saved-workspace__item">
                    <span className="status-dot status-dot--ok" aria-hidden="true" />
                    <div className="saved-workspace__body">
                      <span className="text-1">{campaign.name}</span>
                      <span>{campaign.status.replace(/_/g, ' ')} · {campaignCriteriaSummary(campaign)}</span>
                      {(grouped || householdEnabled || selectedVariant) && (
                        <div className="saved-workspace__proof">
                          {grouped && (
                            <span className="chip chip--neutral chip--compact">×{row.draftCount} drafts</span>
                          )}
                          {grouped && latestSaved && (
                            <span className="muted fs-11">latest {latestSaved}</span>
                          )}
                          {householdEnabled && (
                            <>
                              <span className="chip chip--warning">
                                {suppressedCount.toLocaleString()} co-owner
                                {suppressedCount === 1 ? '' : 's'} suppressed
                              </span>
                              <button
                                type="button"
                                className="evidence-chip"
                                onClick={() => setDrawer(DRAWER_SOURCES.householdRollup)}
                              >
                                <Icon name="link" size={9} className="e-ico" />
                                <span className="evidence-chip__label">household_rollup</span>
                              </button>
                            </>
                          )}
                          {selectedVariant && (
                            <>
                              <span
                                className={`chip ${selectedVariant.generationMode === 'supervisor' ? 'chip--success' : 'chip--neutral'} chip--compact`}
                              >
                                {selectedVariant.generatorLabel}
                              </span>
                              <span className="mono muted fs-11" title={campaign.campaign_id}>
                                campaign {campaign.campaign_id.slice(0, 12)}
                              </span>
                              <span className={`chip ${selectedVariant.provenanceAttached ? 'chip--success' : 'chip--warning'} chip--compact`}>
                                {selectedVariant.provenanceAttached ? 'Provenance attached' : 'Saved provenance only'}
                              </span>
                              <select
                                className="outreach-routing__select"
                                value={selectedVariantName}
                                onChange={(event) => setSelectedCampaignVariants((current) => ({
                                  ...current,
                                  [campaign.campaign_id]: event.target.value,
                                }))}
                                aria-label={`Message variant for ${campaign.name}`}
                              >
                                {variants.map((variant) => (
                                  <option key={variant.variantName} value={variant.variantName}>
                                    Variant {variant.variantName}
                                  </option>
                                ))}
                              </select>
                              <Link
                                to={savedCampaignLeadQueueUrl(campaign, selectedVariant.variantName)}
                                className="btn btn--primary btn--sm"
                                aria-label={`Open ${campaign.name} variant ${selectedVariant.variantName} in Lead Queue`}
                              >
                                Open lead queue
                                <Icon name="chevright" size={12} />
                              </Link>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {preview?.high_intent_leads !== undefined && preview.high_intent_leads > 0 && (
        <div className="lead-cta">
          <div className="lead-cta__body">
            <div className="lead-cta__title">
              {preview.high_intent_leads.toLocaleString()} borrower
              {preview.high_intent_leads === 1 ? '' : 's'} pass the refinance-economics screen for the current filters.
            </div>
            <div className="lead-cta__sub">
              Open the lead queue to review evidence and approve outreach per borrower.
            </div>
          </div>
          <Link to={leadQueueUrl} className="btn btn--primary">
            Open lead queue
            <Icon name="chevright" size={14} />
          </Link>
        </div>
      )}

      <div className="section-actions">
        <Link to={segmentIntelligenceUrl} className="btn">
          Next: segments
          <Icon name="chevright" size={14} />
        </Link>
      </div>
    </PageShell>
  );
}
