import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import type { PortfolioPreview } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { KpiCard } from '../components/mortgage/KpiCard';
import { ApprovalBanner } from '../components/mortgage/ApprovalBanner';
import { Button } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { FilterSelect } from '../components/ui/FilterSelect';
import { DRAWER_SOURCES } from '../lib/drawerSources';

/**
 * Portfolio Builder — prototype `.surface` + `.filter-row` composition.
 * Filter dropdowns drive a population estimate; KPI grid reads from
 * /api/portfolio/preview. "Generate Approval Required Outreach" is the
 * primary forward motion into segment intelligence.
 */

// Slice 9: GEO options refreshed to the 6-state Delta Share footprint
// (IL / CA / FL / TX / WA / CO) so the filter reads like a real book of
// business, not a single-metro slice. Chicago MSA is the default since
// IL is the largest state in the footprint and our default anchor.
const FILTER_GROUPS: Array<{ label: string; key: string; options: string[] }> = [
  { label: 'GEO',          key: 'geo',      options: ['Chicago MSA', 'All 6 states', 'Texas', 'CA + FL + TX', 'IL + CA + WA'] },
  { label: 'OCCUPANCY',    key: 'occ',      options: ['Owner-occupied', 'Non-owner-occupied', 'All'] },
  { label: 'LIEN STATUS',  key: 'lien',     options: ['Open 1st lien', 'Open HELOC', 'Free & clear', 'Any'] },
  { label: 'RELATIONSHIP', key: 'rel',      options: ['All', 'Current customer', 'Former customer', 'Competitor customer'] },
  { label: 'PRODUCT',      key: 'product',  options: ['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention'] },
  { label: 'EQUITY',       key: 'equity',   options: ['≥ 15%', '≥ 25%', '≥ 40%', 'Any'] },
];

function formatDelta(pct: number | null | undefined): string | undefined {
  if (pct === null || pct === undefined) return undefined;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}% vs 7d ago`;
}

export default function PortfolioBuilder() {
  const [preview, setPreview] = useState<PortfolioPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [building, setBuilding] = useState<boolean>(false);
  const [filters, setFilters] = useState<Record<string, string>>({
    geo: 'Chicago MSA',
    occ: 'Owner-occupied',
    lien: 'Open 1st lien',
    rel: 'All',
    product: 'All products',
    equity: '≥ 15%',
  });

  const runBuild = (criteria: Record<string, string>, signal?: { cancelled: boolean }) => {
    setBuilding(true);
    setPreviewError(null);
    api
      .portfolioPreview(criteria)
      .then((p) => {
        if (signal?.cancelled) return;
        setPreview(p);
      })
      .catch((err: unknown) => {
        if (signal?.cancelled) return;
        setPreview(null);
        setPreviewError(
          err instanceof Error
            ? `Couldn't load portfolio preview: ${err.message}`
            : "Couldn't load portfolio preview.",
        );
      })
      .finally(() => {
        if (!signal?.cancelled) setBuilding(false);
      });
  };

  useEffect(() => {
    const signal = { cancelled: false };
    runBuild(filters, signal);
    return () => {
      signal.cancelled = true;
    };
    // Intentionally runs once on mount; user drives subsequent runs via "Run build".
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setFilter = (key: string) => (next: string) => setFilters((f) => ({ ...f, [key]: next }));

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
            {FILTER_GROUPS.map((g) => (
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
              variant="primary"
              icon="play"
              onClick={() => runBuild(filters)}
              disabled={building}
              aria-busy={building}
            >
              {building ? 'Running…' : 'Run build'}
            </Button>
          </div>

          {previewError && (
            <div
              role="alert"
              style={{
                marginTop: 14,
                padding: '10px 12px',
                border: '1px solid var(--signal-danger)',
                borderRadius: 'var(--r-md)',
                color: 'var(--signal-danger)',
                fontSize: 12,
              }}
            >
              {previewError}
            </div>
          )}

          <div className="kpi-row" style={{ marginTop: 20 }}>
            <KpiCard
              label="Marketable population"
              valueAnimated={preview?.marketable_population ?? null}
              trend={preview?.trends?.marketable_population?.series}
              delta={formatDelta(preview?.trends?.marketable_population?.delta_pct)}
              deltaDir={preview?.trends?.marketable_population?.direction}
              source={DRAWER_SOURCES.population}
            />
            <KpiCard
              label="Avg. borrower score"
              valueAnimated={preview?.avg_score ?? null}
              trend={preview?.trends?.avg_score?.series}
              delta={formatDelta(preview?.trends?.avg_score?.delta_pct)}
              deltaDir={preview?.trends?.avg_score?.direction}
              source={DRAWER_SOURCES.nbo}
            />
            <KpiCard
              label="Cost per contact (est.)"
              valueAnimated={preview?.cost_per_contact ?? null}
              format={(n) => `$${n.toFixed(2)}`}
              source={DRAWER_SOURCES.config}
            />
            <KpiCard
              label="Projected contact → app"
              valueAnimated={preview?.projected_contact_to_app ?? null}
              format={(n) => n.toFixed(1)}
              unit="%"
              source={DRAWER_SOURCES.nbo}
            />
          </div>
        </div>
      </div>

      {preview?.high_intent_leads !== undefined && (
        <div style={{ marginTop: 'var(--gap-grid)' }}>
          <ApprovalBanner
            count={preview.high_intent_leads}
            text={`${preview.high_intent_leads.toLocaleString()} borrowers will enter the lead queue for loan-officer review.`}
            approveLabel="Send to loan officers"
          />
        </div>
      )}

      <div style={{ marginTop: 'var(--gap-grid)', display: 'flex', gap: 12 }}>
        <Link to="/segment-intelligence" className="btn btn--primary">
          Next: segments
          <Icon name="chevright" size={14} />
        </Link>
        <Link to="/lead-queue" className="btn">Jump to lead queue</Link>
      </div>
    </PageShell>
  );
}
