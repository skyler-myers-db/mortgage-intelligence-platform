import { useEffect, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { PageShell } from '../components/layout/PageShell';
import { KpiCard } from '../components/mortgage/KpiCard';
import { USChoroplethMap } from '../components/mortgage/USChoroplethMap';
import { PinnedInsights } from '../components/mortgage/PinnedInsights';
import { PortfolioSummaryCard } from '../components/mortgage/PortfolioSummaryCard';
import { AgentActivityLog } from '../components/mortgage/AgentActivityLog';
import { Button, Chip } from '../components/Primitives';
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
import { formatRefreshed } from '../lib/formatRefreshed';
import type { KpiTrend, PortfolioPreview } from '../types';

const FUTURE_MODULES = [
  { code: 'M1', title: 'Pre-Qualified Offer & Self-Serve Accept', desc: 'Borrower-facing pre-qualified offer with one-click accept → compliant application handoff (FCRA firm-offer + RESPA timing).' },
  { code: 'M2', title: 'LO Workbench',          desc: 'Officer assist with explainable borrower guidance.' },
  { code: 'M3', title: 'Underwriting Copilot',  desc: 'Condition handling and exception triage.' },
  { code: 'M4', title: 'Risk & Retention',      desc: 'Portfolio-level retention and recapture.' },
];

export const HOME_PORTFOLIO_PREVIEW_CRITERIA = { marketing_eligibility: 'Any' } as const;
export const APPROVAL_QUEUE_STATE_LABEL = 'current lifecycle state';

export function requestHomePortfolioPreview(signal?: AbortSignal) {
  return api.portfolioPreview(HOME_PORTFOLIO_PREVIEW_CRITERIA, signal);
}

/** Format a signed percent-delta for the KPI delta slot. `null` → undefined
 * so the KpiCard simply hides the delta row. */
function formatDelta(trend: KpiTrend | undefined): string | undefined {
  const pct = trend?.delta_pct;
  if (pct === null || pct === undefined) return undefined;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}% ${trend?.comparison_label ?? 'vs prior snapshot'}`;
}

export default function Home() {
  // Home KPIs read straight from /api/v1/portfolio/preview. While the request is
  // in flight we show skeletons rather than design-time numbers or em-dashes,
  // so normal loading is visually distinct from a genuinely unknown value.
  const { lender } = useApp();
  const navigate = useNavigate();
  const healthCtx = useOptionalHealth();
  // True when the shared health poll has confirmed warehouse / lakebase is
  // down. While that's the case we keep the warming-up tile visible
  // instead of falling into a contradictory red error: the system-wide
  // DegradedBanner already explains the situation, and the per-tile
  // retry budget (30s) is shorter than a typical serverless-warehouse
  // cold start (30-60s). User feedback 2026-04-25: showing both a
  // "reconnecting" banner AND a "couldn't load" red tile makes the app
  // look broken when it is correctly warming up.
  const warehouseDown =
    healthCtx?.health?.dependencies?.warehouse === 'down' ||
    healthCtx?.health?.dependencies?.lakebase === 'down';
  const previousWarehouseDown = useRef(warehouseDown);
  const {
    data: preview,
    warmingUp: previewWarming,
    error: previewErrorObj,
    manualRetry: retryPreview,
  } = useWarmingUpRetry<PortfolioPreview>(
    requestHomePortfolioPreview,
    ['home'],
    { queryKey: queryKeys.homePreview() },
  );
  const previewError = previewErrorObj
    ? previewErrorObj instanceof Error
      ? previewErrorObj.message
      : "Couldn't load portfolio KPIs."
    : null;

  useEffect(() => {
    if (previousWarehouseDown.current && !warehouseDown && previewErrorObj) retryPreview();
    previousWarehouseDown.current = warehouseDown;
  }, [previewErrorObj, retryPreview, warehouseDown]);

  const queued = preview?.high_intent_leads ?? null;
  const kpisLoading = preview === null && !previewError && !previewWarming;

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
      lede="Portfolio KPIs, geography drill-down, and the approval queue. Build a new portfolio, jump to segments, or open a filtered lead queue from the map."
      wideMap
      heroRight={
        <>
          {(() => {
            // R5-19: render viewer-local display text + ISO-UTC title so
            // two operators in different timezones can disambiguate the
            // same screenshot on hover.
            const refreshed = formatRefreshed(preview?.data_refreshed_at);
            if (!refreshed) return null;
            return (
              <Chip variant="neutral" icon="db" title={refreshed.iso}>
                {refreshed.display}
              </Chip>
            );
          })()}
          <Link to="/portfolio-builder" className="btn btn--primary">
            Build a portfolio
            <Icon name="chevright" size={14} />
          </Link>
        </>
      }
    >
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
          tile auto-recovers when health flips back to up (effect
          below increments reloadToken). */}
      {previewError && !previewWarming && warehouseDown && (
        <div
          role="status"
          aria-live="polite"
          className="status-callout"
        >
          Portfolio KPIs are waiting on the analytics warehouse. The page
          will fetch them automatically once the warehouse finishes
          warming up — typically 30–60 seconds after first request.
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
        <div
          role="status"
          className="status-callout status-callout--day-zero"
        >
          <strong>First data refresh pending.</strong>{' '}
          Unity Catalog gold tables are empty. Run{' '}
          <code className="callout-code">
            databricks bundle run mip_refresh_scores -t dev
          </code>{' '}
          to populate them.
        </div>
      )}
      {!isDayZero && !previewWarming && preview?.trend_note && (
        <div role="status" className="status-callout status-callout--info">
          {preview.trend_note}
        </div>
      )}
      {/* Pinned insights (Buyer-Wow #9): operator's pinned Genie answers —
          renders nothing when empty, so it adds no chrome until used. */}
      <PinnedInsights />
      {!isDayZero && !previewWarming && (
        <div className="kpi-row">
          <KpiCard
            label="Marketable population"
            valueAnimated={preview?.marketable_population ?? null}
            trend={preview?.trends?.marketable_population?.series}
            delta={formatDelta(preview?.trends?.marketable_population)}
            deltaDir={preview?.trends?.marketable_population?.direction}
            trendNote={preview?.trends?.marketable_population?.note}
            loading={kpisLoading}
            source={DRAWER_SOURCES.population}
          />
          <KpiCard
            label="Refi economics screen"
            valueAnimated={preview?.high_intent_leads ?? null}
            trend={preview?.trends?.high_intent_leads?.series}
            delta={formatDelta(preview?.trends?.high_intent_leads)}
            deltaDir={preview?.trends?.high_intent_leads?.direction}
            trendNote={preview?.trends?.high_intent_leads?.note}
            loading={kpisLoading}
            source={DRAWER_SOURCES.itm}
          />
            <KpiCard
              label="Opportunity score 75+"
            valueAnimated={preview?.top_tier_opportunities ?? null}
            trend={preview?.trends?.top_tier_opportunities?.series}
            delta={formatDelta(preview?.trends?.top_tier_opportunities)}
            deltaDir={preview?.trends?.top_tier_opportunities?.direction}
            trendNote={preview?.trends?.top_tier_opportunities?.note}
            loading={kpisLoading}
            source={DRAWER_SOURCES.leadScore}
          />
            <KpiCard
              label="Primary offer paths"
            valueAnimated={preview?.offers_recommended ?? null}
            trend={preview?.trends?.offers_recommended?.series}
            delta={formatDelta(preview?.trends?.offers_recommended)}
            deltaDir={preview?.trends?.offers_recommended?.direction}
            trendNote={preview?.trends?.offers_recommended?.note}
            loading={kpisLoading}
            source={DRAWER_SOURCES.nbo}
          />
        </div>
      )}

      {/* "Your book today" — a current-state, plain-English orientation of the
          portfolio, composed deterministically from the preview already loaded
          (no new request, no live AI). Replaces the removed delta briefing with
          an honest snapshot summary. Gated like the KPI row. */}
      {!isDayZero && !previewWarming && !previewError && (
        <PortfolioSummaryCard preview={preview ?? null} loading={kpisLoading} />
      )}

      <div
        role="region"
        aria-label="Approval queue"
        className="approval mt-grid"
      >
        <div className="approval__ico"><Icon name="shield" size={16} /></div>
        <div className="approval__body">
          <div className="approval__title">Approval queue</div>
          <div className="approval__sub">
            {queued !== null
              ? `${queued.toLocaleString()} borrowers pass the refinance-economics screen. ${(
                  preview?.approved_count ?? 0
                ).toLocaleString()} approved and ${(
                  preview?.in_outreach_count ?? 0
                ).toLocaleString()} in outreach in ${APPROVAL_QUEUE_STATE_LABEL}.`
              : 'Borrowers passing the refinance-economics screen are ready for loan-officer review.'}
          </div>
        </div>
        <Link to="/lead-queue?segment=itm" className="btn btn--sm btn--primary">
          Open review queue
          <Icon name="chevright" size={13} />
        </Link>
      </div>

      <div className="section-hdr">
        <div>
          <div className="eyebrow">Geography</div>
          <div className="h-2">State → county → ZIP → borrower</div>
        </div>
      </div>
      <div className="layoutA-grid">
        {/* Home map drills in-place through state/county/ZIP and then opens
            the filtered Lead Queue. Lead Queue remains the source-of-truth
            index for borrower selection. */}
        <USChoroplethMap drillBehavior="filter" />
        <Reveal>
          <AgentActivityLog />
        </Reveal>
      </div>

      <Reveal>
        <div className="section-hdr">
          <div>
            <div className="eyebrow">Roadmap</div>
            <div className="h-2">Planned modules</div>
          </div>
        </div>
        <div className="roadmap-grid">
          {FUTURE_MODULES.map((m) => (
            <div className="surface" key={m.code}>
              <div className="surface__body">
                <div className="eyebrow">{m.code} · planned</div>
                <div className="h-3 mt-2">{m.title}</div>
                <p className="body mt-2">{m.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </Reveal>

      <div className="section-actions">
        <Link to="/portfolio-builder" className="btn btn--primary" aria-label="Build a lead portfolio">
          Build a lead portfolio
        </Link>
        <Link to="/segment-intelligence" className="btn">
          Jump to segments
        </Link>
        {/* SPA navigation (audit P1-6): window.location.href here was the only
            full-page reload in the app — a visible white flash on a hero CTA. */}
        <Button variant="ghost" size="default" onClick={() => navigate('/ask-genie')} icon="sparkle">
          Ask Genie
        </Button>
      </div>

      <Reveal>
        <div className="brand-signature" aria-hidden="true">
          <EntradaWordmark height={44} />
        </div>
      </Reveal>
    </PageShell>
  );
}
