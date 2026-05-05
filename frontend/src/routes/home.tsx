import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { PageShell } from '../components/layout/PageShell';
import { KpiCard } from '../components/mortgage/KpiCard';
import { USChoroplethMap } from '../components/mortgage/USChoroplethMap';
import { AgentActivityLog } from '../components/mortgage/AgentActivityLog';
import { Button, Chip } from '../components/Primitives';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { Icon } from '../components/Icon';
import { Reveal } from '../components/fx/Reveal';
import { api, isAbortError, isWarmingUpError, dependencyLabel } from '../lib/api';
import type { WarmingUpState } from '../lib/useWarmingUpRetry';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { useApp } from '../components/AppContext';
import { useOptionalHealth } from '../components/HealthProvider';
import { EntradaWordmark } from '../components/brand/Entrada';
import { formatRefreshed } from '../lib/formatRefreshed';
import type { KpiTrend, PortfolioPreview } from '../types';

const FUTURE_MODULES = [
  { code: 'M1', title: 'Pipeline Optimization', desc: 'Lead → app → approval throughput and stalls.' },
  { code: 'M2', title: 'LO Workbench',          desc: 'Officer assist with explainable next-best-action.' },
  { code: 'M3', title: 'Underwriting Copilot',  desc: 'Condition handling and exception triage.' },
  { code: 'M4', title: 'Risk & Retention',      desc: 'Portfolio-level retention and recapture.' },
];

/** Format a signed percent-delta for the KPI delta slot. `null` → undefined
 * so the KpiCard simply hides the delta row. */
function formatDelta(trend: KpiTrend | undefined): string | undefined {
  const pct = trend?.delta_pct;
  if (pct === null || pct === undefined) return undefined;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}% ${trend?.comparison_label ?? 'vs prior snapshot'}`;
}

export default function Home() {
  // Home KPIs read straight from /api/portfolio/preview. While the request is
  // in flight we show an em-dash placeholder rather than design-time numbers
  // so the surface never presents a plausible-but-fake value. The KpiCard
  // component interprets a null `valueAnimated` as "render em-dash".
  const { lender } = useApp();
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
  const [preview, setPreview] = useState<PortfolioPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // Cold-start retry state for the portfolio-preview tile. Non-null =
  // we're in a 503 retry loop driven by warehouse/lakebase auto-suspend.
  // Rendered as an inline "Portfolio KPIs — warehouse warming up
  // (attempt N of 6)…" block in place of the KPI row so the rest of the
  // page (map, activity log) stays reactive. 2026-04-23 UX fix.
  const [previewWarming, setPreviewWarming] = useState<WarmingUpState | null>(null);
  // Reload token — incrementing it via the Retry button re-runs the
  // portfolio-preview fetch without a full route reload. Hole-finder
  // finding #1, 2026-04-23.
  const [reloadToken, setReloadToken] = useState<number>(0);
  // When the shared health poll flips warehouse from down → up after
  // we've fallen out of the per-tile retry loop, auto-trigger a fresh
  // fetch. Without this, the tile stays in the "warming up" copy
  // forever even though the warehouse is now ready. User feedback
  // 2026-04-25: the page should self-recover, not require a click.
  useEffect(() => {
    if (!warehouseDown && previewError) {
      setReloadToken((n) => n + 1);
    }
  }, [warehouseDown, previewError]);
  useEffect(() => {
    // AbortController replaces the legacy `cancelled` guard so an unmount
    // or filter change actually cancels the in-flight fetch (not just the
    // setState). Round-2 hole-finder #10/#11, 2026-04-23.
    const ctrl = new AbortController();
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    const MAX_ATTEMPTS = 6;
    const INTERVAL_MS = 5000;

    const runAttempt = async (attempt: number): Promise<void> => {
      if (cancelled) return;
      try {
        const p = await api.portfolioPreview({}, ctrl.signal);
        if (cancelled) return;
        setPreview(p);
        setPreviewError(null);
        setPreviewWarming(null);
      } catch (err: unknown) {
        if (cancelled || isAbortError(err)) return;
        if (isWarmingUpError(err) && attempt < MAX_ATTEMPTS) {
          setPreviewWarming({
            dependency: err.dependency,
            label: `${dependencyLabel(err.dependency)} warming up`,
            attempt: attempt + 1,
            maxAttempts: MAX_ATTEMPTS,
            correlationId: err.correlationId,
          });
          setPreview(null);
          setPreviewError(null);
          timeoutId = setTimeout(() => {
            void runAttempt(attempt + 1);
          }, INTERVAL_MS);
          return;
        }
        setPreview(null);
        setPreviewWarming(null);
        setPreviewError(
          err instanceof Error ? err.message : "Couldn't load portfolio KPIs.",
        );
      }
    };

    void runAttempt(1);

    return () => {
      cancelled = true;
      if (timeoutId !== null) clearTimeout(timeoutId);
      ctrl.abort();
    };
  }, [reloadToken]);

  const queued = preview?.high_intent_leads ?? null;

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
      lede="Portfolio KPIs, geography drill-down, and the approval queue. Build a new portfolio, jump to segments, or open a borrower dossier from the map."
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
            onClick={() => setReloadToken((n) => n + 1)}
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
      {!isDayZero && !previewWarming && (
        <div className="kpi-row">
          <KpiCard
            label="Marketable population"
            valueAnimated={preview?.marketable_population ?? null}
            trend={preview?.trends?.marketable_population?.series}
            delta={formatDelta(preview?.trends?.marketable_population)}
            deltaDir={preview?.trends?.marketable_population?.direction}
            source={DRAWER_SOURCES.population}
          />
          <KpiCard
            label="High-intent leads"
            valueAnimated={preview?.high_intent_leads ?? null}
            trend={preview?.trends?.high_intent_leads?.series}
            delta={formatDelta(preview?.trends?.high_intent_leads)}
            deltaDir={preview?.trends?.high_intent_leads?.direction}
            source={DRAWER_SOURCES.itm}
          />
          <KpiCard
            label="Top-tier opportunities"
            valueAnimated={preview?.top_tier_opportunities ?? null}
            trend={preview?.trends?.top_tier_opportunities?.series}
            delta={formatDelta(preview?.trends?.top_tier_opportunities)}
            deltaDir={preview?.trends?.top_tier_opportunities?.direction}
            source={DRAWER_SOURCES.leadScore}
          />
          <KpiCard
            label="Offers recommended"
            valueAnimated={preview?.offers_recommended ?? null}
            trend={preview?.trends?.offers_recommended?.series}
            delta={formatDelta(preview?.trends?.offers_recommended)}
            deltaDir={preview?.trends?.offers_recommended?.direction}
            source={DRAWER_SOURCES.nbo}
          />
        </div>
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
              ? `${queued.toLocaleString()} borrowers awaiting loan-officer approval.`
              : 'Borrowers awaiting loan-officer approval.'}
          </div>
        </div>
      </div>

      <div className="section-hdr">
        <div>
          <div className="eyebrow">Geography</div>
          <div className="h-2">State → county → ZIP → borrower</div>
        </div>
      </div>
      <div className="layoutA-grid">
        {/* Home map drills in-place (state → county → ZIP → borrower deep
            link) rather than bouncing to /lead-queue. Matches the
            prototype's hero-map behavior; the Lead Queue remains the
            source-of-truth index and is one click away via the CTAs
            below. Fix D, 2026-04-23. */}
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
        <Button variant="ghost" size="default" onClick={() => (window.location.href = '/ask-genie')} icon="sparkle">
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
