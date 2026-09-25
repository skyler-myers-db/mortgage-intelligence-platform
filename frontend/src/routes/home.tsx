import { Link } from 'react-router';
import { PageShell } from '../components/layout/PageShell';
import { KpiCard } from '../components/mortgage/KpiCard';
import { USChoroplethMap } from '../components/mortgage/USChoroplethMap';
import { useMapSelectionParams } from '../components/mortgage/useMapSelectionParams';
import { PinnedInsights } from '../components/mortgage/PinnedInsights';
import { HomeAnswerBand } from '../components/mortgage/HomeAnswerBand';
import { Chip } from '../components/Primitives';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { Icon } from '../components/Icon';
import { Reveal } from '../components/fx/Reveal';
import { api } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import { queryKeys } from '../lib/queryKeys';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { useApp } from '../components/AppContext';
import { useOptionalHealth } from '../components/HealthProvider';
import { EntradaWordmark } from '../components/brand/Entrada';
import { signedPct } from '../lib/formatters';
import { parseBackendTimestamp } from '../lib/time';
import { Timestamp } from '../components/ui/Timestamp';
import type { HomeSummary, KpiTrend, PortfolioPreview } from '../types';
import { HIGH_OPPORTUNITY_KPI_LABEL } from '../lib/opportunityScore';
import { ApprovalQueueBanner } from './home.approval-banner';
import './home.css';

/** Home's one primary action: the ranked queue the answer band previews. */
export const HOME_PRIMARY_CTA = { label: "Review today's top leads", href: '/lead-queue' } as const;

export const HOME_PORTFOLIO_PREVIEW_CRITERIA = { marketing_eligibility: 'Any' } as const;
export { APPROVAL_QUEUE_STATE_LABEL } from './home.approval-banner';

export function requestHomePortfolioPreview(signal?: AbortSignal) {
  return api.portfolioPreview(HOME_PORTFOLIO_PREVIEW_CRITERIA, signal);
}

export function requestHomeSummary(signal?: AbortSignal) {
  return api.homeSummary(signal);
}

/** Format a signed percent-delta for the KPI delta slot. `null` → undefined
 * so the KpiCard simply hides the delta row. */
function formatDelta(trend: KpiTrend | undefined): string | undefined {
  const pct = trend?.delta_pct;
  if (pct === null || pct === undefined) return undefined;
  return `${signedPct(pct)} ${trend?.comparison_label ?? 'vs prior snapshot'}`;
}

export function HomeDayZeroStatus({ canAccessAdmin }: { canAccessAdmin: boolean }) {
  return (
    <div role="status" className="status-callout status-callout--day-zero">
      <strong>First data refresh pending.</strong>{' '}
      Unity Catalog gold tables are empty.{' '}
      {canAccessAdmin ? (
        <>
          Start and monitor the refresh in{' '}
          <Link to="/admin-config#data-operations">Admin Data Operations</Link>.
        </>
      ) : (
        'Contact an administrator to start and monitor the refresh.'
      )}
    </div>
  );
}

export default function Home() {
  // Home KPIs read straight from /api/v1/portfolio/preview. While the request is
  // in flight we show skeletons rather than design-time numbers or em-dashes,
  // so normal loading is visually distinct from a genuinely unknown value.
  const { lender, canAccessAdmin } = useApp();
  const [mapSelection, setMapSelection] = useMapSelectionParams();
  const healthCtx = useOptionalHealth();
  // True when the shared health poll has confirmed warehouse / lakebase is
  // down: a real outage (a routine serverless resume reports `resuming`, not
  // `down`, and never reaches this). While that's the case we keep a calm
  // callout instead of falling into a contradictory red error: the
  // system-wide DegradedBanner already explains the situation, and an outage
  // can outlast the per-tile retry budget (30 s). User feedback 2026-04-25:
  // showing both a "reconnecting" banner AND a "couldn't load" red tile makes
  // the app look broken.
  const warehouseDown =
    healthCtx?.health?.dependencies?.warehouse === 'down' ||
    healthCtx?.health?.dependencies?.lakebase === 'down';
  const {
    data: preview,
    warmingUp: previewWarming,
    error: previewErrorObj,
    manualRetry: retryPreview,
  } = useWarmingUpRetry<PortfolioPreview>(
    requestHomePortfolioPreview,
    { queryKey: queryKeys.homePreview() },
  );
  const previewError = previewErrorObj
    ? previewErrorObj instanceof Error
      ? previewErrorObj.message
      : "Couldn't load portfolio KPIs."
    : null;

  // S4 "since your last login" summary: additive fetch — the KPI row above
  // owns the degraded-state story, so a warming/erroring summary simply
  // renders nothing rather than stacking a second callout.
  const {
    data: summary,
    warmingUp: summaryWarming,
    error: summaryError,
  } = useWarmingUpRetry<HomeSummary>(requestHomeSummary, {
    queryKey: queryKeys.homeSummary(),
  });
  const summaryLoading = !summary && !summaryError && !summaryWarming;

  const queued = preview?.high_intent_leads ?? null;
  const kpisLoading = preview === null && !previewError && !previewWarming;
  // The KPI row stays mounted in its loading state through a warm-up (audit
  // states-10): unmounting it collapsed the four cards and the answer band
  // jumped up, then down again when the numbers landed.
  const kpiRowLoading = kpisLoading || (preview === null && Boolean(previewWarming));

  // Day-0 detection (R5-20): trust the server-authoritative
  // ``day_zero`` flag on PortfolioPreview, which keys off
  // ``COUNT(*) FROM mip.gold.lead_population``.
  //
  // R6-06: the two-field fallback inference (marketable_population ===
  // 0 && data_refreshed_at === null) was dead code -- the backend
  // default is ``day_zero: false`` so the "older server" case cannot
  // exist in practice. Dead code that returned a wrong answer when
  // the server said False but the count happened to be 0 (e.g. a
  // criteria that matched zero borrowers on a populated workspace),
  // so the banner could lie. Removed; we trust the server.
  const isDayZero = preview?.day_zero === true;

  return (
    <PageShell
      eyebrow={lender}
      title="Who should we contact, why now, and with what offer?"
      lede="Today's briefing answers all three; each evidence chip opens the source behind its figure."
      wideMap
      heroRight={
        <>
          {/* Freshness reads as relative age ("Refreshed 3 hours ago"); the
              <time> carries the instant and an absolute-UTC title, so two
              operators in different timezones can still disambiguate a
              screenshot on hover (R5-19; 2026-09-21 audit responsive-07). */}
          {parseBackendTimestamp(preview?.data_refreshed_at) && (
            <Chip variant="neutral" icon="db">
              Refreshed <Timestamp value={preview?.data_refreshed_at} />
            </Chip>
          )}
          {/* Exactly one primary action on Home (2026-09-21 audit flow-05):
              the ranked queue the answer band previews. Building a portfolio
              comes after reviewing leads, so it moved to the side panel's
              secondary actions. */}
          <Link to={HOME_PRIMARY_CTA.href} className="btn btn--primary">
            {HOME_PRIMARY_CTA.label}
            <Icon name="chevright" size={14} />
          </Link>
        </>
      }
    >
      {/* One grid wraps the whole stack so every card sits --gap-grid from
          its neighbour (2026-09-21 audit visual-06; home.css). */}
      <div className="home-stack">
        {previewWarming && (
          <WarmingUpBlock
            state={previewWarming}
            title="Portfolio KPIs loading"
            compact
          />
        )}
        {/* When the health poll confirms the warehouse / lakebase is down,
            we keep the warming-up copy visible instead of dropping into
            the red error. The system-wide DegradedBanner already says
            "reconnecting"; a contradictory red tile underneath made the
            app look broken when it was correctly cold-starting. The
            tile recovers on its own: on the down -> up edge HealthProvider
            refetches every mounted query that failed because of the
            recovered dependency (components/healthRecovery.ts), on every
            route, so Home no longer carries a private recovery effect. */}
        {previewError && !previewWarming && warehouseDown && (
          <div
            role="status"
            aria-live="polite"
            className="status-callout"
          >
            {/* A true outage, so no duration is promised (delivery-01). */}
            Portfolio KPIs are waiting on the analytics warehouse, which is
            not answering right now. They load on their own as soon as it is
            back.
          </div>
        )}
        {previewError && !previewWarming && !warehouseDown && (
          <div
            role="alert"
            className="status-callout status-callout--danger"
          >
            <span>Couldn&apos;t load portfolio KPIs: {previewError}</span>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={retryPreview}
              aria-label="Retry loading portfolio KPIs"
            >
              Retry
            </button>
          </div>
        )}
        {isDayZero && (
          <HomeDayZeroStatus canAccessAdmin={canAccessAdmin} />
        )}
        {!isDayZero && !previewWarming && preview?.trend_note && (
          <div role="status" className="status-callout status-callout--info">
            {preview.trend_note}
          </div>
        )}
        {!isDayZero && (
          <div className="kpi-row">
            {/* "Addressable", not "marketable": HOME_PORTFOLIO_PREVIEW_CRITERIA
                asks for `marketing_eligibility: 'Any'`, so this KPI counts the
                whole reachable book, not the governed marketable subset
                (~76K of ~5.16M). 2026-08-07 data audit — the number is right,
                the label was claiming a narrower predicate than it applied. */}
            <KpiCard
              label="Addressable population"
              valueAnimated={preview?.marketable_population ?? null}
              trend={preview?.trends?.marketable_population?.series}
              delta={formatDelta(preview?.trends?.marketable_population)}
              deltaDir={preview?.trends?.marketable_population?.direction}
              trendNote={preview?.trends?.marketable_population?.note}
              loading={kpiRowLoading}
              source={DRAWER_SOURCES.population}
            />
            <KpiCard
              label="Refi economics screen"
              valueAnimated={preview?.high_intent_leads ?? null}
              trend={preview?.trends?.high_intent_leads?.series}
              delta={formatDelta(preview?.trends?.high_intent_leads)}
              deltaDir={preview?.trends?.high_intent_leads?.direction}
              trendNote={preview?.trends?.high_intent_leads?.note}
              loading={kpiRowLoading}
              source={DRAWER_SOURCES.itm}
            />
            <KpiCard
              label={HIGH_OPPORTUNITY_KPI_LABEL}
              valueAnimated={preview?.top_tier_opportunities ?? null}
              trend={preview?.trends?.top_tier_opportunities?.series}
              delta={formatDelta(preview?.trends?.top_tier_opportunities)}
              deltaDir={preview?.trends?.top_tier_opportunities?.direction}
              trendNote={preview?.trends?.top_tier_opportunities?.note}
              loading={kpiRowLoading}
              source={DRAWER_SOURCES.leadScore}
            />
            <KpiCard
              label="Primary offer paths"
              valueAnimated={preview?.offers_recommended ?? null}
              trend={preview?.trends?.offers_recommended?.series}
              delta={formatDelta(preview?.trends?.offers_recommended)}
              deltaDir={preview?.trends?.offers_recommended?.direction}
              trendNote={preview?.trends?.offers_recommended?.note}
              loading={kpiRowLoading}
              source={DRAWER_SOURCES.nbo}
            />
          </div>
        )}

        {/* The answer band (flow-05): WHO / WHY NOW / WHAT TO OFFER under one
            briefing line. It merges the old "Since your last login" and
            "Your book today" cards (visual-06). Gated like the KPI row: it
            stays mounted, in its loading state, through a warm-up, so the
            hero never collapses and pops back in (delivery-01). */}
        {!isDayZero && (
          <HomeAnswerBand
            preview={preview ?? null}
            previewLoading={kpiRowLoading}
            summary={summary ?? null}
            summaryLoading={summaryLoading || Boolean(summaryWarming)}
          />
        )}

        {/* The geography hero beside a side panel, the prototype's
            `.layoutA-grid` pairing (MapPanel + RightRail). Home map drills
            in-place from state to ZIP and then opens the filtered Lead
            Queue. There is no county rung — the Cotality share carries one
            county FIPS per state, so county_fips_5 is NULL across gold (see
            USChoroplethMap's design-contract note). Lead Queue remains the
            source-of-truth index for borrower selection. */}
        <div className="layoutA-grid home-geo">
          {/* 520 like Segments: beside the approval queue the map is ~760px
              wide, its legend is a strip under the stage, and 420 left the
              US about 250px tall. */}
          <USChoroplethMap
            drillBehavior="filter"
            height={520}
            selection={mapSelection}
            onSelectionChange={setMapSelection}
          />
          <div className="home-side">
            {/* States BOTH numbers — contactable of whole-book — because its
                button opens the contactable-only queue (flow-v1). */}
            <ApprovalQueueBanner
              screenCount={queued}
              approvedCount={preview?.approved_count ?? 0}
              inOutreachCount={preview?.in_outreach_count ?? 0}
            />
            {/* Pinned insights (Buyer-Wow #9): operator's pinned Genie
                answers — renders nothing when empty. */}
            <PinnedInsights />
            <div className="section-actions">
              <Link to="/portfolio-builder" className="btn">
                Build a portfolio
              </Link>
              <Link to="/segment-intelligence" className="btn">
                Explore segments
              </Link>
              <Link to="/ask-genie" className="btn btn--ghost">
                <Icon name="sparkle" size={14} />
                Ask Genie a question
              </Link>
            </div>
          </div>
        </div>
      </div>

      <Reveal revealKey="home:brand-signature">
        <div className="brand-signature" aria-hidden="true">
          <EntradaWordmark height={44} />
        </div>
      </Reveal>
    </PageShell>
  );
}
