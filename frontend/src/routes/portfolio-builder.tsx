import { type ChangeEvent, useCallback, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router';
import { api } from '../lib/api';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type {
  CampaignListResponse,
  CampaignRecommendationResponse,
  PortfolioCreateResponse,
  PortfolioPreview,
  CampaignPerformanceFunnelResponse,
} from '../types';
import { PageShell } from '../components/layout/PageShell';
import { CampaignPrefillBanner } from '../components/mortgage/CampaignPrefillBanner';
import { KpiCard } from '../components/mortgage/KpiCard';
import { Button, SurfaceTitle } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { useApp } from '../components/AppContext';
import { FilterSelect } from '../components/ui/FilterSelect';
import { AsyncStatus } from '../components/ui/AsyncState';
import { DescribedErrorBody, preloadDescribedError } from '../components/ui/DescribedError';
import { Field } from '../components/ui/Field';
import { parseCampaignPrefill } from '../lib/campaignPrefill';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { useFootprint } from '../components/FootprintProvider';
import { useUnsavedGuard } from '../hooks/useUnsavedGuard';
import { queryKeys } from '../lib/queryKeys';
import { campaignMutationKeys, useCreateCampaign } from '../lib/mutations/campaigns';
import { intentFingerprint, useIntentRequestIds } from '../lib/mutations/requestIds';
import { toast } from '../lib/toast';
import { copyLink } from '../lib/copyLink';
import { formatCount } from '../lib/formatters';
import { formatDateTimeShort } from '../lib/time';
import { RoiProjector, StateMultiSelect } from './portfolio-builder.components';
import { CampaignSetupPanel } from './portfolio-builder.campaign-setup';
import { CampaignBuildGuard, SavedCampaignsPanel } from './portfolio-builder.governance';
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
  dayZeroSafe,
  defaultGeographyForOptions,
  formatDelta,
  isDayZero,
  parseFiltersFromUrl,
  parseStateCodesFromUrl,
  sanitizeFootprintStateCodes,
  type CampaignSetupState,
} from './portfolio-builder.logic';
import { campaignSetupsEqual, portfolioUnsavedMessage } from './portfolio-builder.unsaved';
import { useCampaignDraft } from './portfolio-builder.draft';
import { HIGH_OPPORTUNITY_KPI_LABEL } from '../lib/opportunityScore';
import { populationKpiLabel } from '../lib/populationLabels';

/**
 * Portfolio Builder — prototype `.surface` + `.filter-row` composition.
 * Filter dropdowns drive a population estimate; KPI grid reads from
 * /api/portfolio/preview. "Generate Approval Required Outreach" is the
 * primary forward motion into segment intelligence.
 */

function isoDateDaysAgo(days: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

export default function PortfolioBuilder() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { canAccessAdmin } = useApp();
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
  // Success feedback is a shell toast (audit states-07); a failed save keeps
  // its inline alert in the naming form until the next attempt, worded from
  // the caught error (states-04: a 403, a 409 and an outage read differently).
  const [saveFailed, setSaveFailed] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  // Inline naming form for "Save build". Re-audit #3 P1 (2026-06-12): the
  // previous native prompt() dialog is SYNCHRONOUS — it blocks the renderer
  // main thread (a hard freeze under any CDP/Playwright session, and an
  // un-themeable OS dialog for humans). Enterprise polish + e2e testability
  // both want this in-page async form instead. A source pin in
  // portfolio-builder.save.test.tsx keeps the blocking API out for good.
  const [savePanelOpen, setSavePanelOpen] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [saveValidationError, setSaveValidationError] = useState<string | null>(null);
  // The setup last persisted with a build: the unsaved-changes baseline.
  const [savedCampaignSetup, setSavedCampaignSetup] = useState<CampaignSetupState>(DEFAULT_CAMPAIGN_SETUP);
  const previewCriteria = useMemo(
    () => buildPreviewCriteria(committedFilters, committedStateCodes),
    [committedFilters, committedStateCodes],
  );
  // critic-v3: the setup is this tab's draft, bound to the criteria its variants were applied under.
  const {
    setup: campaignSetup,
    setSetup: setCampaignSetup,
    restore: draftRestore,
    reset: resetDraft,
    saved: draftSaved,
  } = useCampaignDraft(JSON.stringify(previewCriteria), savedCampaignSetup);
  const campaignBuildConfig = useMemo(() => {
    const config = buildCampaignConfig(campaignSetup);
    return {
      suppression_policy: config.suppression_policy,
      household_dedup: config.household_dedup,
    };
  }, [campaignSetup]);
  const campaignsQuery = useQuery<CampaignListResponse>({
    queryKey: queryKeys.campaigns(),
    queryFn: ({ signal }) => api.campaigns(signal),
    retry: false,
  });

  // Cold-start warming-up loop. Re-runs whenever committedFilters
  // changes (via Run build or URL navigation). 6 retries / 5s apart =
  // 30s of auto-retry before surfacing the red error path.
  const committedKey = useMemo(
    () => JSON.stringify({
      filters: committedFilters,
      stateCodes: committedStateCodes,
      campaignBuildConfig,
    }),
    [campaignBuildConfig, committedFilters, committedStateCodes],
  );
  const previewQuery = useWarmingUpRetry<PortfolioPreview>(
    (signal) => api.portfolioPreview(previewCriteria, signal, campaignBuildConfig),
    { queryKey: queryKeys.portfolioPreview([committedKey]), keepPreviousData: true },
  );
  const { data: preview, warmingUp, error, isFetching: previewFetching } = previewQuery;
  const building = preview === null && warmingUp === null && error === null;
  // Re-audit #3 P1 (2026-06-12): `building` is false during a background
  // refetch of an UNCHANGED build key (stale preview still rendered), so
  // "Run build → Save build within ~1s" found Save enabled mid-build.
  // Everything that must not race an in-flight build gates on this.
  const buildInFlight = building || previewFetching;
  // The server owns both the treatment limit and the eligibility decision.
  // Equality to true is deliberate: an older or malformed response fails
  // closed for persistence while Run build and recommendation exploration
  // remain available.
  const campaignBuildEligible = preview?.campaign_build_eligible === true;
  const campaignBuildLimit = preview?.campaign_build_limit ?? 10_000;
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
    queryFn: ({ signal }) => api.campaignRecommendation(previewCriteria, signal),
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
    | 'subjectA'
    | 'subjectB'
    | 'bodyA'
    | 'bodyB'
  >) => (
    event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => setCampaignSetup((current) => ({
    ...current,
    [key]: event.target.value,
  }));
  const toggleHouseholdDedup = useCallback(() => {
    setCampaignSetup((current) => ({
      ...current,
      marketHouseholdTogether: !current.marketHouseholdTogether,
    }));
  }, [setCampaignSetup]);
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
  }, [recommendationQuery.data, setCampaignSetup]);
  const buildDirty = useMemo(
    () =>
      JSON.stringify({ filters, stateCodes }) !==
      JSON.stringify({ filters: committedFilters, stateCodes: committedStateCodes }),
    [committedFilters, committedStateCodes, filters, stateCodes],
  );
  // Audit states-05: filters not yet run and a setup not yet saved survive a
  // route leave or tab close only through the operator's choice.
  const unsavedMessage = portfolioUnsavedMessage(
    buildDirty,
    !campaignSetupsEqual(campaignSetup, savedCampaignSetup),
  );
  useUnsavedGuard(unsavedMessage !== null, unsavedMessage ?? undefined);

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
    await copyLink(window.location.href, {
      success: 'Build link copied',
      successDetail: 'Anyone with access opens this exact build.',
      failure: 'Copy failed',
      failureDetail: 'The browser blocked clipboard access. Copy the address bar instead.',
    });
  }, [buildDirty]);

  // The governed save on the mutation layer (stack-09 item 2): pessimistic,
  // one Idempotency-Key per intent. A reopened panel is a new intent (the
  // session counter is part of the fingerprint), so it mints a new key, while
  // "try again" after a failure resends the same one.
  const queryClient = useQueryClient();
  const createCampaign = useCreateCampaign(queryClient);
  const saveRequestIds = useIntentRequestIds();
  const [saveSession, setSaveSession] = useState(0);
  const onOpenSavePanel = useCallback(() => {
    if (buildDirty || buildInFlight || preview?.campaign_build_eligible !== true) return;
    setSaveSession((session) => session + 1);
    setSaveName(`Portfolio build ${formatDateTimeShort(new Date())}`);
    setSaveValidationError(null);
    setSaveFailed(false);
    setSavePanelOpen(true);
    // A failed save words its alert from the shared vocabulary chunk: load it with the intent.
    void preloadDescribedError();
  }, [buildDirty, buildInFlight, preview?.campaign_build_eligible]);

  const saving = createCampaign.isPending;
  const onConfirmSave = useCallback(async () => {
    const name = saveName.trim();
    // Synchronous latch: a create is already on the wire (a double submit).
    if (!name || queryClient.isMutating({ mutationKey: campaignMutationKeys.create }) > 0 || buildInFlight) return;
    if (preview?.campaign_build_eligible !== true) {
      setSaveValidationError(
        `This build is not eligible for the governed ${formatCount(campaignBuildLimit)}-contact campaign limit. Refine the filters and run it again.`,
      );
      setSaveFailed(true);
      return;
    }
    const copyError = campaignCopyValidationError(campaignSetup);
    if (copyError) {
      setSaveValidationError(copyError);
      setSaveFailed(true);
      return;
    }
    setSaveValidationError(null);
    setSaveFailed(false);
    // Re-audit #4 (2026-06-12): the panel used to close BEFORE the await,
    // so a failed save silently discarded the operator's typed name. Keep
    // the panel (and the name) until the save actually succeeds; on failure
    // the form stays open with the typed name and a "Save failed" hint so
    // the operator can retry without re-typing.
    const criteria = previewCriteria;
    const config = buildCampaignConfig(campaignSetup);
    const intent = intentFingerprint('campaign-create', saveSession, JSON.stringify({ name, criteria, config }));
    let created: PortfolioCreateResponse | undefined;
    try {
      // Resolves after the saved-campaign list is re-read (the mutation's onSuccess).
      created = await createCampaign.mutateAsync({ name, criteria, config, requestId: saveRequestIds.idFor(intent) });
    } catch (err) {
      setSaveError(err);
      setSaveFailed(true);
      return;
    }
    saveRequestIds.settle(intent);
    setSavedCampaignSetup(campaignSetup);
    draftSaved();
    setSavePanelOpen(false);
    toast.success('Build saved', { detail: name, auditEventId: created?.audit_event_id ?? null });
  }, [
    campaignBuildLimit,
    campaignSetup,
    buildInFlight,
    createCampaign,
    draftSaved,
    preview?.campaign_build_eligible,
    previewCriteria,
    queryClient,
    saveName,
    saveRequestIds,
    saveSession,
  ]);

  // When the URL changes (browser back/forward), reconcile local state
  // and refetch so the KPI grid reflects the navigation. We only
  // refetch if the URL-derived filters actually differ from local
  // state — otherwise setState from onRunBuild would cause an
  // unnecessary second fetch. Both reconciliations below run in render
  // ("store the previous input", no effect), keyed on exactly the inputs the
  // effects they replaced were keyed on, in the same order.
  const urlFilters = useMemo(
    () => parseFiltersFromUrl(searchParams, defaultFilters, targetLenderOptions),
    [defaultFilters, searchParams, targetLenderOptions],
  );
  const urlStateCodes = useMemo(
    () => parseStateCodesFromUrl(searchParams, footprint.states),
    [footprint.states, searchParams],
  );
  const [reconciledUrl, setReconciledUrl] = useState({ filters: urlFilters, stateCodes: urlStateCodes });
  if (reconciledUrl.filters !== urlFilters || reconciledUrl.stateCodes !== urlStateCodes) {
    setReconciledUrl({ filters: urlFilters, stateCodes: urlStateCodes });
    const differs = URL_FILTER_KEYS.some((k) => urlFilters[k] !== filters[k]);
    const stateDiffers = urlStateCodes.join(',') !== stateCodes.join(',');
    if (differs || stateDiffers) {
      setFilters(urlFilters);
      setCommittedFilters(urlFilters);
      setStateCodes(urlStateCodes);
      setCommittedStateCodes(urlStateCodes);
    }
  }
  // A footprint change drops states outside it. The initial state already
  // holds the parse of the same footprint, so the first pass is a no-op and
  // starts from the mount's inputs.
  const [sanitizedFor, setSanitizedFor] = useState({ states: footprint.states, geoOptionsKey });
  if (sanitizedFor.states !== footprint.states || sanitizedFor.geoOptionsKey !== geoOptionsKey) {
    setSanitizedFor({ states: footprint.states, geoOptionsKey });
    const sanitize = (codes: string[]) => sanitizeFootprintStateCodes(codes, footprint.states);
    setStateCodes(sanitize);
    setCommittedStateCodes(sanitize);
  }

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
              <SurfaceTitle>Filters</SurfaceTitle>
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
              {buildDirty ? 'Run before sharing' : 'Share this build'}
            </Button>
            <Button
              variant="ghost"
              size="default"
              icon="doc"
              onClick={onOpenSavePanel}
              disabled={buildDirty || buildInFlight || !campaignBuildEligible}
              aria-label="Save current portfolio build"
              aria-expanded={savePanelOpen}
              data-testid="portfolio-save-build"
            >
              {buildDirty
                ? 'Run before saving'
                : buildInFlight
                ? 'Build running…'
                : !campaignBuildEligible
                ? 'Refine before saving'
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
              <Field className="field--inline" label="Build name">
                {(control) => (
                  <input
                    {...control}
                    className="form-input save-build-form__input"
                    value={saveName}
                    onChange={(e) => setSaveName(e.target.value)}
                    maxLength={80}
                    autoFocus
                    data-testid="portfolio-save-name"
                  />
                )}
              </Field>
              <Button
                variant="primary"
                size="sm"
                type="submit"
                icon="check"
                disabled={saveName.trim().length === 0 || saving || buildInFlight || !campaignBuildEligible}
                aria-busy={saving || buildInFlight}
                data-testid="portfolio-save-confirm"
              >
                {saving ? 'Saving…' : buildInFlight ? 'Rechecking…' : 'Save'}
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
              {saveFailed && (
                <span className="save-build-form__error" role="alert">
                  {saveValidationError ?? (
                    <>Save failed: <DescribedErrorBody error={saveError} subject="the build save" /> Your name is kept; try again.</>
                  )}
                </span>
              )}
            </form>
          )}
          {targetLenderStatus !== 'live' && (
            <div className="filter-row__hint muted">
              Target lien holder options are limited until live borrower lender aliases finish refreshing.
            </div>
          )}

          {/* states-04: warming and failure in the shared buyer-safe vocabulary, never error.message. */}
          {(error !== null || (warmingUp !== null && preview === null)) && (
            <div className="mt-4">
              <AsyncStatus query={previewQuery} subject="Portfolio preview" compact />
            </div>
          )}
          {warmingUp && preview !== null && (
            <div className="mt-4">
              <span className="chip chip--neutral chip--compact stable-status-chip">
                {warmingUp.label} ({warmingUp.attempt}/{warmingUp.maxAttempts})
              </span>
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

          {preview && <CampaignBuildGuard preview={preview} />}

          <div className="kpi-row kpi-row--spaced">
            {/* Label AND evidence chip follow the CONTACTABILITY criterion the
                preview was built with. At the default ("Eligible only") this
                count has the governed contactability gate pushed down, so it
                is the marketable subset and the chip must say so — it read
                "Addressable population", the predicate Home applies, which is
                the opposite gate. Switch CONTACTABILITY to Any / Suppressed
                only and both the label and the chip follow. */}
            <KpiCard
              label={populationKpiLabel(committedFilters.marketing_eligibility)}
              valueAnimated={dayZeroSafe(preview, preview?.marketable_population)}
              trend={preview?.trends?.marketable_population?.series}
              delta={formatDelta(preview?.trends?.marketable_population)}
              deltaDir={preview?.trends?.marketable_population?.direction}
              trendNote={preview?.trends?.marketable_population?.note}
              loading={building}
              source={
                committedFilters.marketing_eligibility === 'Eligible only'
                  ? DRAWER_SOURCES.populationMarketable
                  : DRAWER_SOURCES.population
              }
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
        draftRestore={draftRestore}
        onResetDraft={resetDraft}
        onFieldChange={setCampaignField}
        onNumericFieldCommit={(key, value) => setCampaignSetup((current) => ({
          ...current,
          [key]: value,
        }))}
        onToggleHouseholdDedup={toggleHouseholdDedup}
        onRegenerate={() => void recommendationQuery.refetch()}
        onApply={applyRecommendation}
      />

      <SavedCampaignsPanel query={campaignsQuery} />

      {preview?.high_intent_leads !== undefined && preview.high_intent_leads > 0 && (
        <div className="lead-cta">
          <div className="lead-cta__body">
            <div className="lead-cta__title">
              {formatCount(preview.high_intent_leads)} borrower
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
