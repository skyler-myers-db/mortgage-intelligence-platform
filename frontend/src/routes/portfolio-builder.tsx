import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { PortfolioPreview } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { KpiCard } from '../components/mortgage/KpiCard';
import { Button } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { FilterSelect } from '../components/ui/FilterSelect';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { useFootprint } from '../components/FootprintProvider';

/**
 * Portfolio Builder — prototype `.surface` + `.filter-row` composition.
 * Filter dropdowns drive a population estimate; KPI grid reads from
 * /api/portfolio/preview. "Generate Approval Required Outreach" is the
 * primary forward motion into segment intelligence.
 */

// Non-GEO filter groups are tenant-invariant. The GEO group is built at
// render time from the FootprintProvider (see `buildGeoOptions` below) so
// the "All N states" label and the state-triple presets reflect the
// tenant's real footprint rather than a hardcoded 6-state literal.
// Keys MUST match PortfolioCriteria in backend/schemas/portfolio.py. The
// earlier mismatch (`geo` vs `geography`, `occ` vs `occupancy`, etc.) made
// every filter a no-op because Pydantic silently ignored unknown fields.
const NON_GEO_FILTER_GROUPS: Array<{ label: string; key: string; options: string[] }> = [
  { label: 'OCCUPANCY',    key: 'occupancy',            options: ['Owner-occupied', 'Non-owner-occupied', 'All'] },
  { label: 'LIEN STATUS',  key: 'lien_status',          options: ['Open 1st lien', 'Open HELOC', 'Free & clear', 'Any'] },
  { label: 'RELATIONSHIP', key: 'lender_relationship',  options: ['All', 'Current customer', 'Former customer', 'Competitor customer'] },
  { label: 'PRODUCT',      key: 'product',              options: ['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention'] },
  { label: 'EQUITY',       key: 'min_equity_pct_label', options: ['≥ 15%', '≥ 25%', '≥ 40%', 'Any'] },
];

/**
 * Build the GEO-dropdown options for the current tenant footprint.
 *
 * Emits (in order):
 *   1. The curated "Chicago MSA" anchor (only if IL is in the footprint;
 *      Summit's default tenant leads with Chicago so this entry preserves
 *      the established UX).
 *   2. "All N states" — the whole-footprint option, where N is the live
 *      count (so a 4-state tenant sees "All 4 states", not "All 6").
 *   3. Each state by its backend-provided state_name (so TX is "Texas",
 *      CA is "California", etc.). This replaces the prior hardcoded
 *      per-state entries ("Texas") + ad-hoc triples ("CA + FL + TX",
 *      "IL + CA + WA") that were only meaningful for Summit.
 */
function buildGeoOptions(
  states: ReadonlyArray<{ state_code: string; state_name: string }>,
): string[] {
  const opts: string[] = [];
  const hasIllinois = states.some((s) => s.state_code.toUpperCase() === 'IL');
  if (hasIllinois) opts.push('Chicago MSA');
  opts.push(`All ${states.length} states`);
  for (const s of states) opts.push(s.state_name);
  return opts;
}

// Default filter values keyed by the short codes the existing local
// state uses. Keeping these keys stable is a guardrail from the
// round-2 audit — the backend schema already ignores unknown fields,
// and renaming them mid-slice would be a scope creep.
const DEFAULT_FILTERS: Record<string, string> = {
  geo: 'Chicago MSA',
  occ: 'Owner-occupied',
  lien: 'Open 1st lien',
  rel: 'All',
  product: 'All products',
  equity: '≥ 15%',
};

/**
 * URL search-param keys we round-trip. One per filter + the reload
 * token so the "Run build" commit is reproducible from a deep link.
 * These match the field names in DEFAULT_FILTERS so the URL reads
 * naturally ("?geo=Chicago+MSA&occ=Owner-occupied&..."). Unknown
 * params are ignored on parse; defaults fill in the rest.
 */
const URL_FILTER_KEYS = ['geo', 'occ', 'lien', 'rel', 'product', 'equity'] as const;

function parseFiltersFromUrl(sp: URLSearchParams): Record<string, string> {
  const out: Record<string, string> = { ...DEFAULT_FILTERS };
  for (const k of URL_FILTER_KEYS) {
    const v = sp.get(k);
    if (v !== null && v.length > 0) out[k] = v;
  }
  return out;
}

function buildUrlFromFilters(filters: Record<string, string>): URLSearchParams {
  const sp = new URLSearchParams();
  for (const k of URL_FILTER_KEYS) {
    const v = filters[k];
    // Skip defaults so the URL stays compact and shareable — a user
    // who hasn't touched a filter won't have 6 redundant params in
    // their address bar.
    if (v !== undefined && v !== DEFAULT_FILTERS[k]) {
      sp.set(k, v);
    }
  }
  return sp;
}

function formatDelta(pct: number | null | undefined): string | undefined {
  if (pct === null || pct === undefined) return undefined;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}% vs 7d ago`;
}

/**
 * Day-0 detection: trust the server-authoritative ``day_zero`` flag on
 * PortfolioPreview (R5-20). When true, the KPI grid swaps raw 0 values
 * for `null` so KpiCard renders an em-dash and the banner explains why.
 *
 * R6-06: the two-field fallback inference (marketable_population === 0
 * && data_refreshed_at === null) was dead code -- the backend always
 * emits ``day_zero`` (default False), so the "older server" case cannot
 * exist. The inference also returned a wrong answer for the valid case
 * where a filter happens to match zero borrowers on a populated
 * workspace (e.g. "investors in WY"). Removed; we trust the server.
 */
function isDayZero(preview: PortfolioPreview | null): boolean {
  return preview?.day_zero === true;
}

function dayZeroSafe(
  preview: PortfolioPreview | null,
  value: number | null | undefined,
): number | null {
  if (isDayZero(preview)) {
    return null;
  }
  return value ?? null;
}

export default function PortfolioBuilder() {
  const [searchParams, setSearchParams] = useSearchParams();
  const footprint = useFootprint();
  // Build the GEO dropdown from the tenant footprint. Memoised so the
  // FilterSelect doesn't get a fresh options array on every render (it
  // would be identity-stable for same-footprint re-renders).
  const geoOptions = useMemo(
    () => buildGeoOptions(footprint.states),
    [footprint.states],
  );
  const filterGroups = useMemo(
    () => [
      { label: 'GEO', key: 'geography', options: geoOptions },
      ...NON_GEO_FILTER_GROUPS,
    ],
    [geoOptions],
  );
  // Initialize from URL so deep-links and browser back/forward work. On
  // mount we also trigger a build, so a shared link reproduces the
  // exact KPI grid the sender saw. Round-2 hole-finder #16, 2026-04-23.
  const [filters, setFilters] = useState<Record<string, string>>(() =>
    parseFiltersFromUrl(searchParams),
  );
  // The "committed" filter payload drives the useWarmingUpRetry hook.
  // `filters` tracks the dropdown state, `committedFilters` is what the
  // KPI grid reflects — only updated via onRunBuild or URL navigation.
  // This preserves the prototype UX: filter changes don't refetch; the
  // "Run build" button is the explicit commit point.
  const [committedFilters, setCommittedFilters] = useState<Record<string, string>>(
    () => parseFiltersFromUrl(searchParams),
  );
  const [copyHint, setCopyHint] = useState<'idle' | 'copied' | 'failed'>('idle');

  // Cold-start warming-up loop. Re-runs whenever committedFilters
  // changes (via Run build or URL navigation). 6 retries / 5s apart =
  // 30s of auto-retry before surfacing the red error path.
  const committedKey = useMemo(
    () => JSON.stringify(committedFilters),
    [committedFilters],
  );
  const {
    data: preview,
    warmingUp,
    error,
    manualRetry: retryBuild,
  } = useWarmingUpRetry<PortfolioPreview>(
    (signal) => api.portfolioPreview(committedFilters, signal),
    [committedKey],
  );
  const building = preview === null && warmingUp === null && error === null;
  const previewError = error
    ? error instanceof Error
      ? `Couldn't load portfolio preview: ${error.message}`
      : "Couldn't load portfolio preview."
    : null;

  const setFilter = (key: string) => (next: string) => setFilters((f) => ({ ...f, [key]: next }));

  /**
   * Commit the current filter state: push to URL, then refetch. The
   * URL is the source of truth for a shareable build so we update it
   * on the explicit "Run build" click rather than on every dropdown
   * change (which would pollute browser history with every keystroke).
   */
  const onRunBuild = useCallback(() => {
    setSearchParams(buildUrlFromFilters(filters), { replace: false });
    setCommittedFilters(filters);
  }, [filters, setSearchParams]);

  /**
   * Copy the current URL to the clipboard. Falls back to a failed
   * hint if the Clipboard API is unavailable (Safari private mode,
   * old browsers). The URL already reflects the last committed build
   * because onRunBuild wrote to it.
   */
  const onCopyLink = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopyHint('copied');
    } catch {
      setCopyHint('failed');
    }
  }, []);

  useEffect(() => {
    if (copyHint === 'idle') return;
    const t = window.setTimeout(() => setCopyHint('idle'), 1800);
    return () => window.clearTimeout(t);
  }, [copyHint]);

  // When the URL changes (browser back/forward), reconcile local state
  // and refetch so the KPI grid reflects the navigation. We only
  // refetch if the URL-derived filters actually differ from local
  // state — otherwise setState from onRunBuild would cause an
  // unnecessary second fetch.
  const urlFilters = useMemo(
    () => parseFiltersFromUrl(searchParams),
    [searchParams],
  );
  useEffect(() => {
    const differs = URL_FILTER_KEYS.some((k) => urlFilters[k] !== filters[k]);
    if (differs) {
      setFilters(urlFilters);
      setCommittedFilters(urlFilters);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlFilters]);

  return (
    <PageShell
      eyebrow="Portfolio Builder"
      title="Build a borrower population"
      lede="Apply geography, occupancy, lien, relationship, product, and equity filters, then run the build. The KPI grid shows size, average score, and projected conversion."
    >
      <div className="surface">
        <div className="surface__hdr" style={{ justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div
              style={{
                width: 28,
                height: 28,
                borderRadius: 8,
                background: 'var(--accent-soft)',
                color: 'var(--accent)',
                display: 'grid',
                placeItems: 'center',
              }}
            >
              <Icon name="target" size={14} />
            </div>
            <div>
              <div className="h-4">Filters</div>
              <div className="muted" style={{ fontSize: 12 }}>
                Filter the population, run the build, review KPIs.
              </div>
            </div>
          </div>
        </div>
        <div className="surface__body">
          <div className="filter-row">
            {filterGroups.map((g) => (
              <FilterSelect
                key={g.key}
                label={g.label}
                value={filters[g.key]}
                options={g.options}
                onChange={setFilter(g.key)}
              />
            ))}
            <div style={{ flex: 1 }} />
            <Button
              variant="ghost"
              size="default"
              icon="link"
              onClick={() => void onCopyLink()}
              aria-label="Copy shareable URL for the current build"
              data-testid="portfolio-copy-link"
            >
              {copyHint === 'copied'
                ? 'Link copied'
                : copyHint === 'failed'
                ? 'Copy failed'
                : 'Share this build'}
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

          {warmingUp && (
            <div style={{ marginTop: 14 }}>
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
              style={{
                marginTop: 14,
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
              style={{
                marginTop: 14,
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

          <div className="kpi-row" style={{ marginTop: 20 }}>
            <KpiCard
              label="Marketable population"
              valueAnimated={dayZeroSafe(preview, preview?.marketable_population)}
              trend={preview?.trends?.marketable_population?.series}
              delta={formatDelta(preview?.trends?.marketable_population?.delta_pct)}
              deltaDir={preview?.trends?.marketable_population?.direction}
              source={DRAWER_SOURCES.population}
            />
            <KpiCard
              label="Avg. borrower score"
              valueAnimated={dayZeroSafe(preview, preview?.avg_score)}
              trend={preview?.trends?.avg_score?.series}
              delta={formatDelta(preview?.trends?.avg_score?.delta_pct)}
              deltaDir={preview?.trends?.avg_score?.direction}
              source={DRAWER_SOURCES.nbo}
            />
            <KpiCard
              label="Top-tier opportunities"
              valueAnimated={dayZeroSafe(preview, preview?.top_tier_opportunities)}
              trend={preview?.trends?.top_tier_opportunities?.series}
              delta={formatDelta(preview?.trends?.top_tier_opportunities?.delta_pct)}
              deltaDir={preview?.trends?.top_tier_opportunities?.direction}
              source={DRAWER_SOURCES.nbo}
            />
            <KpiCard
              label="Offers recommended"
              valueAnimated={dayZeroSafe(preview, preview?.offers_recommended)}
              trend={preview?.trends?.offers_recommended?.series}
              delta={formatDelta(preview?.trends?.offers_recommended?.delta_pct)}
              deltaDir={preview?.trends?.offers_recommended?.direction}
              source={DRAWER_SOURCES.nbo}
            />
          </div>
        </div>
      </div>

      {preview?.high_intent_leads !== undefined && preview.high_intent_leads > 0 && (
        <div
          style={{
            marginTop: 'var(--gap-grid)',
            padding: '14px 16px',
            border: '1px solid var(--line-1)',
            borderRadius: 'var(--r-md)',
            background: 'var(--bg-1)',
            display: 'flex',
            alignItems: 'center',
            gap: 14,
          }}
        >
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, color: 'var(--text-1)' }}>
              {preview.high_intent_leads.toLocaleString()} high-intent borrower
              {preview.high_intent_leads === 1 ? '' : 's'} match the current filters.
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              Open the lead queue to review evidence and approve outreach per borrower.
            </div>
          </div>
          <Link to="/lead-queue" className="btn btn--primary">
            Open lead queue
            <Icon name="chevright" size={14} />
          </Link>
        </div>
      )}

      <div style={{ marginTop: 'var(--gap-grid)', display: 'flex', gap: 12 }}>
        <Link to="/segment-intelligence" className="btn">
          Next: segments
          <Icon name="chevright" size={14} />
        </Link>
      </div>
    </PageShell>
  );
}
