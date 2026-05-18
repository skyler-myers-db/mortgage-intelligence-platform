import { type ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { CampaignListResponse, CampaignSummary, PortfolioPreview } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { KpiCard } from '../components/mortgage/KpiCard';
import { Button } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { FilterSelect } from '../components/ui/FilterSelect';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { useFootprint } from '../components/FootprintProvider';
import { queryKeys } from '../lib/queryKeys';
import { StateMultiSelect } from './portfolio-builder.components';
import {
  BASE_DEFAULT_FILTERS,
  DEFAULT_CAMPAIGN_SETUP,
  NON_GEO_FILTER_GROUPS,
  URL_FILTER_KEYS,
  buildCampaignConfig,
  buildDefaultCampaignSetup,
  buildGeoOptions,
  buildLeadQueueUrlFromFilters,
  buildPreviewCriteria,
  buildUrlFromFilters,
  campaignCriteriaSummary,
  dayZeroSafe,
  defaultGeographyForOptions,
  formatDelta,
  isDayZero,
  parseFiltersFromUrl,
  parseStateCodesFromUrl,
  type CampaignSetupState,
} from './portfolio-builder.logic';

/**
 * Portfolio Builder — prototype `.surface` + `.filter-row` composition.
 * Filter dropdowns drive a population estimate; KPI grid reads from
 * /api/portfolio/preview. "Generate Approval Required Outreach" is the
 * primary forward motion into segment intelligence.
 */

export default function PortfolioBuilder() {
  const [searchParams, setSearchParams] = useSearchParams();
  const footprint = useFootprint();
  const configOptionsQuery = useConfigOptionsQuery();
  const targetLenderOptions = useMemo(() => {
    const values = configOptionsQuery.data?.target_lender_refs?.filter(Boolean);
    return values && values.length > 0 ? values : ['All'];
  }, [configOptionsQuery.data?.target_lender_refs]);
  const targetLenderStatus = configOptionsQuery.isError
    ? 'unavailable'
    : configOptionsQuery.data?.target_lender_refs_status ?? 'loading';
  const lenderName = configOptionsQuery.data?.lender_name?.trim() || 'configured lender';
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
    parseFiltersFromUrl(searchParams, defaultFilters),
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
    () => parseFiltersFromUrl(searchParams, defaultFilters),
  );
  const [committedStateCodes, setCommittedStateCodes] = useState<string[]>(() =>
    parseStateCodesFromUrl(searchParams, footprint.states),
  );
  const [copyHint, setCopyHint] = useState<'idle' | 'copied' | 'failed'>('idle');
  const [saveHint, setSaveHint] = useState<'idle' | 'saved' | 'failed'>('idle');
  const [campaignSetup, setCampaignSetup] = useState<CampaignSetupState>(DEFAULT_CAMPAIGN_SETUP);
  const campaignSetupDefaultRef = useRef<CampaignSetupState>(DEFAULT_CAMPAIGN_SETUP);
  const currentDefaultCampaignSetup = useMemo(
    () => buildDefaultCampaignSetup(lenderName),
    [lenderName],
  );
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
  } = useWarmingUpRetry<PortfolioPreview>(
    (signal) => api.portfolioPreview(buildPreviewCriteria(committedFilters, committedStateCodes), signal),
    [committedKey],
    { queryKey: queryKeys.portfolioPreview([committedKey]) },
  );
  const building = preview === null && warmingUp === null && error === null;
  const previewError = error
    ? error instanceof Error
      ? `Couldn't load portfolio preview: ${error.message}`
      : "Couldn't load portfolio preview."
    : null;

  const setFilter = (key: string) => (next: string) => setFilters((f) => ({ ...f, [key]: next }));
  const setCampaignField = (key: keyof CampaignSetupState) => (
    event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => setCampaignSetup((current) => ({ ...current, [key]: event.target.value }));
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

  const onSaveBuild = useCallback(async () => {
    if (buildDirty) return;
    const defaultName = `Portfolio build ${new Date().toLocaleString()}`;
    const name = window.prompt('Name this build', defaultName)?.trim();
    if (!name) return;
    try {
      await api.portfolioCreate(
        name,
        buildPreviewCriteria(committedFilters, committedStateCodes),
        buildCampaignConfig(campaignSetup),
      );
      await refetchCampaigns();
      setSaveHint('saved');
    } catch {
      setSaveHint('failed');
    }
  }, [buildDirty, campaignSetup, committedFilters, committedStateCodes, refetchCampaigns]);

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

  useEffect(() => {
    const previousDefault = campaignSetupDefaultRef.current;
    campaignSetupDefaultRef.current = currentDefaultCampaignSetup;
    setCampaignSetup((current) => (
      JSON.stringify(current) === JSON.stringify(previousDefault)
        ? currentDefaultCampaignSetup
        : current
    ));
  }, [currentDefaultCampaignSetup]);

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
      return next.length === footprint.states.length ? [] : next;
    };
    setStateCodes(sanitize);
    setCommittedStateCodes(sanitize);
  }, [footprint.states, geoOptionsKey]);

  const leadQueueUrl = useMemo(() => {
    return buildLeadQueueUrlFromFilters(committedFilters, committedStateCodes, targetLenderOptions);
  }, [committedFilters, committedStateCodes, targetLenderOptions]);

  return (
    <PageShell
      eyebrow="Portfolio Builder"
      title="Build a borrower population"
      lede="Apply geography, occupancy, lien, relationship, product, and equity filters, then run the build. The KPI grid shows size, average score, and projected conversion."
    >
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
              onClick={() => void onSaveBuild()}
              disabled={buildDirty || building}
              aria-label="Save current portfolio build"
              data-testid="portfolio-save-build"
            >
              {saveHint === 'saved'
                ? 'Build saved'
                : saveHint === 'failed'
                ? 'Save failed'
                : buildDirty
                ? 'Run before saving'
                : 'Save build'}
            </Button>
            <Button
              variant="primary"
              icon="play"
              onClick={onRunBuild}
              disabled={building}
              aria-busy={building}
            >
              {building ? 'Running…' : 'Run build'}
            </Button>
          </div>
          {targetLenderStatus !== 'live' && (
            <div className="filter-row__hint muted">
              Target lien holder options are limited until live borrower lender aliases finish refreshing.
            </div>
          )}

          {warmingUp && (
            <div className="mt-4">
              <WarmingUpBlock
                state={warmingUp}
                title="Portfolio preview loading"
                compact
              />
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
              Unity Catalog gold tables are empty. Run{' '}
              <code className="callout-code">
                databricks bundle run mip_refresh_scores -t dev
              </code>{' '}
              to populate them.
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
              label="Top-tier opportunities"
              valueAnimated={dayZeroSafe(preview, preview?.top_tier_opportunities)}
              trend={preview?.trends?.top_tier_opportunities?.series}
              delta={formatDelta(preview?.trends?.top_tier_opportunities)}
              deltaDir={preview?.trends?.top_tier_opportunities?.direction}
              trendNote={preview?.trends?.top_tier_opportunities?.note}
              loading={building}
              source={DRAWER_SOURCES.leadScore}
            />
            <KpiCard
              label="Offers recommended"
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

      <div className="surface mt-4">
        <div className="surface__hdr surface__hdr--split">
          <div className="surface__hdr-main">
            <div className="surface__icon">
              <Icon name="send" size={14} />
            </div>
            <div>
              <div className="h-4">Campaign setup</div>
              <div className="muted fs-12">
                Eligible-only suppression, channel sequence, holdout, send window, and ROI inputs.
              </div>
            </div>
          </div>
          <span className="chip chip--success">eligible only · 30d cap</span>
        </div>
        <div className="surface__body">
          <div className="campaign-setup">
            <label className="campaign-setup__field">
              <span>Subject A</span>
              <input
                className="form-input"
                value={campaignSetup.subjectA}
                onChange={setCampaignField('subjectA')}
                maxLength={120}
              />
            </label>
            <label className="campaign-setup__field">
              <span>Subject B</span>
              <input
                className="form-input"
                value={campaignSetup.subjectB}
                onChange={setCampaignField('subjectB')}
                maxLength={120}
              />
            </label>
            <label className="campaign-setup__field campaign-setup__field--wide">
              <span>Body angle A</span>
              <textarea
                className="form-input campaign-setup__textarea"
                value={campaignSetup.bodyA}
                onChange={setCampaignField('bodyA')}
                maxLength={700}
              />
            </label>
            <label className="campaign-setup__field campaign-setup__field--wide">
              <span>Body angle B</span>
              <textarea
                className="form-input campaign-setup__textarea"
                value={campaignSetup.bodyB}
                onChange={setCampaignField('bodyB')}
                maxLength={700}
              />
            </label>
            <label className="campaign-setup__field">
              <span>Holdout %</span>
              <input
                className="form-input"
                inputMode="decimal"
                value={campaignSetup.holdoutPct}
                onChange={setCampaignField('holdoutPct')}
              />
            </label>
            <label className="campaign-setup__field">
              <span>Send start</span>
              <input
                className="form-input"
                type="time"
                value={campaignSetup.startLocal}
                onChange={setCampaignField('startLocal')}
              />
            </label>
            <label className="campaign-setup__field">
              <span>Send end</span>
              <input
                className="form-input"
                type="time"
                value={campaignSetup.endLocal}
                onChange={setCampaignField('endLocal')}
              />
            </label>
            <label className="campaign-setup__field">
              <span>Budget</span>
              <input
                className="form-input"
                inputMode="decimal"
                value={campaignSetup.budget}
                onChange={setCampaignField('budget')}
                placeholder="optional"
              />
            </label>
            <label className="campaign-setup__field">
              <span>Email cost</span>
              <input
                className="form-input"
                inputMode="decimal"
                value={campaignSetup.emailCost}
                onChange={setCampaignField('emailCost')}
              />
            </label>
            <label className="campaign-setup__field">
              <span>SMS cost</span>
              <input
                className="form-input"
                inputMode="decimal"
                value={campaignSetup.smsCost}
                onChange={setCampaignField('smsCost')}
              />
            </label>
            <label className="campaign-setup__field">
              <span>Mail cost</span>
              <input
                className="form-input"
                inputMode="decimal"
                value={campaignSetup.mailCost}
                onChange={setCampaignField('mailCost')}
              />
            </label>
          </div>
          <div className="campaign-setup__meta">
            <span>Email → SMS after 3 days → direct mail after 10 days</span>
            <span>Tue-Thu · borrower local time</span>
          </div>
        </div>
      </div>

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
              {campaigns.slice(0, 8).map((campaign) => (
                <div key={campaign.campaign_id} className="saved-workspace__item">
                  <span className="status-dot status-dot--ok" aria-hidden="true" />
                  <div className="saved-workspace__body">
                    <span className="text-1">{campaign.name}</span>
                    <span>{campaign.status.replace(/_/g, ' ')} · {campaignCriteriaSummary(campaign)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {preview?.high_intent_leads !== undefined && preview.high_intent_leads > 0 && (
        <div className="lead-cta">
          <div className="lead-cta__body">
            <div className="lead-cta__title">
              {preview.high_intent_leads.toLocaleString()} high-intent borrower
              {preview.high_intent_leads === 1 ? '' : 's'} match the current filters.
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
        <Link to="/segment-intelligence" className="btn">
          Next: segments
          <Icon name="chevright" size={14} />
        </Link>
      </div>
    </PageShell>
  );
}
