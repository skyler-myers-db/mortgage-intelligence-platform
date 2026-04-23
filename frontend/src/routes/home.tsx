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
import { EntradaWordmark } from '../components/brand/Entrada';
import { formatRefreshed } from '../lib/formatRefreshed';
import type { PortfolioPreview } from '../types';

const FUTURE_MODULES = [
  { code: 'M1', title: 'Pipeline Optimization', desc: 'Lead → app → approval throughput and stalls.' },
  { code: 'M2', title: 'LO Workbench',          desc: 'Officer assist with explainable next-best-action.' },
  { code: 'M3', title: 'Underwriting Copilot',  desc: 'Condition handling and exception triage.' },
  { code: 'M4', title: 'Risk & Retention',      desc: 'Portfolio-level retention and recapture.' },
];

/** Format a signed percent-delta for the KPI delta slot. `null` → undefined
 * so the KpiCard simply hides the delta row. */
function formatDelta(pct: number | null | undefined): string | undefined {
  if (pct === null || pct === undefined) return undefined;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}% vs 7d ago`;
}

export default function Home() {
  // Home KPIs read straight from /api/portfolio/preview. While the request is
  // in flight we show an em-dash placeholder rather than design-time numbers
  // so the surface never presents a plausible-but-fake value. The KpiCard
  // component interprets a null `valueAnimated` as "render em-dash".
  const { lender } = useApp();
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

  // Day-0 detection (hole-finder round 2 #13, 2026-04-23): on a fresh
  // customer workspace `mip.gold.funnel_snapshot_daily` is empty and
  // `mip.gold.borrower_360` has no rows yet. The preview then comes back
  // as zeroes with a null timestamp — which renders as "0 / 0 / 0 / 0"
  // and looks like honest-but-sad real data. Catch that exact shape and
  // show an empty-state banner so the presenter knows to run the bundle,
  // not explain why the pipeline says nothing.
  //
  // R5-20: prefer the server-authoritative ``day_zero`` flag, which
  // keys off ``COUNT(*) FROM mip.gold.lead_population`` and is immune
  // to the partial-CTAS-roll window where snapshot_at lags behind
  // borrower_360. Fall back to the two-field inference for pre-R5-20
  // servers that don't emit the flag.
  const isDayZero =
    preview !== null
    && (
      preview.day_zero === true
      || (
        preview.day_zero === undefined
        && preview.marketable_population === 0
        && preview.data_refreshed_at === null
      )
    );

  return (
    <PageShell
      eyebrow={lender}
      title="Today"
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
      {previewError && !previewWarming && (
        <div
          role="alert"
          style={{
            marginBottom: 'var(--gap-grid)',
            padding: '10px 12px',
            border: '1px solid var(--signal-danger)',
            borderRadius: 'var(--r-md)',
            color: 'var(--signal-danger)',
            fontSize: 12,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
          }}
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
          style={{
            marginBottom: 'var(--gap-grid)',
            padding: '12px 14px',
            border: '1px solid var(--line-2)',
            borderRadius: 'var(--r-md)',
            background: 'var(--bg-1)',
            fontSize: 13,
            color: 'var(--text-1)',
          }}
        >
          <strong>First data refresh pending.</strong>{' '}
          Unity Catalog gold tables are empty. Run{' '}
          <code
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              padding: '1px 6px',
              borderRadius: 4,
              background: 'var(--bg-2)',
            }}
          >
            databricks bundle run mip_refresh_scores -t dev
          </code>{' '}
          to populate them.
        </div>
      )}
      {!isDayZero && !previewWarming && (
        <div className="kpi-row">
          <KpiCard
            label="Marketable population"
            valueAnimated={preview?.marketable_population ?? null}
            trend={preview?.trends?.marketable_population?.series}
            delta={formatDelta(preview?.trends?.marketable_population?.delta_pct)}
            deltaDir={preview?.trends?.marketable_population?.direction}
            source={DRAWER_SOURCES.population}
          />
          <KpiCard
            label="High-intent leads"
            valueAnimated={preview?.high_intent_leads ?? null}
            trend={preview?.trends?.high_intent_leads?.series}
            delta={formatDelta(preview?.trends?.high_intent_leads?.delta_pct)}
            deltaDir={preview?.trends?.high_intent_leads?.direction}
            source={DRAWER_SOURCES.itm}
          />
          <KpiCard
            label="Top-tier opportunities"
            valueAnimated={preview?.top_tier_opportunities ?? null}
            trend={preview?.trends?.top_tier_opportunities?.series}
            delta={formatDelta(preview?.trends?.top_tier_opportunities?.delta_pct)}
            deltaDir={preview?.trends?.top_tier_opportunities?.direction}
            source={DRAWER_SOURCES.nbo}
          />
          <KpiCard
            label="Offers recommended"
            valueAnimated={preview?.offers_recommended ?? null}
            trend={preview?.trends?.offers_recommended?.series}
            delta={formatDelta(preview?.trends?.offers_recommended?.delta_pct)}
            deltaDir={preview?.trends?.offers_recommended?.direction}
            source={DRAWER_SOURCES.nbo}
          />
        </div>
      )}

      <div
        className="approval"
        role="region"
        aria-label="Approval queue"
        style={{ marginTop: 'var(--gap-grid)' }}
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
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: 'var(--gap-grid)',
          }}
        >
          {FUTURE_MODULES.map((m) => (
            <div className="surface" key={m.code}>
              <div className="surface__body">
                <div className="eyebrow">{m.code} · planned</div>
                <div className="h-3" style={{ marginTop: 6 }}>{m.title}</div>
                <p className="body" style={{ marginTop: 6 }}>{m.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </Reveal>

      <div style={{ marginTop: 'var(--gap-grid)', display: 'flex', gap: 12 }}>
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
          <EntradaWordmark fontSize={36} />
        </div>
      </Reveal>
    </PageShell>
  );
}
