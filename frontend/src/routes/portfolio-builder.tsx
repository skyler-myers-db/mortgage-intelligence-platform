import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import type { PortfolioPreview } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { KpiCard } from '../components/mortgage/KpiCard';
import { ApprovalBanner } from '../components/mortgage/ApprovalBanner';
import { Chip, Button } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { FilterSelect } from '../components/ui/FilterSelect';
import { DRAWER_SOURCES } from '../mocks/demoData';

/**
 * Portfolio Builder — prototype `.surface` + `.filter-row` composition.
 * Filter dropdowns drive a population estimate; KPI grid reads from
 * /api/portfolio/preview. "Generate Approval Required Outreach" is the
 * primary forward motion into segment intelligence.
 */

const FILTER_GROUPS: Array<{ label: string; key: string; options: string[] }> = [
  { label: 'GEO',          key: 'geo',      options: ['Atlanta MSA', 'All US', 'Texas', 'CA + TX + FL', 'Top 20 MSAs'] },
  { label: 'OCCUPANCY',    key: 'occ',      options: ['Owner-occupied', 'Non-owner-occupied', 'All'] },
  { label: 'LIEN STATUS',  key: 'lien',     options: ['Open 1st lien', 'Open HELOC', 'Free & clear', 'Any'] },
  { label: 'RELATIONSHIP', key: 'rel',      options: ['All', 'Current customer', 'Former customer', 'Competitor customer'] },
  { label: 'PRODUCT',      key: 'product',  options: ['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention'] },
  { label: 'EQUITY',       key: 'equity',   options: ['≥ 15%', '≥ 25%', '≥ 40%', 'Any'] },
];

export default function PortfolioBuilder() {
  const [preview, setPreview] = useState<PortfolioPreview | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({
    geo: 'Atlanta MSA',
    occ: 'Owner-occupied',
    lien: 'Open 1st lien',
    rel: 'All',
    product: 'All products',
    equity: '≥ 15%',
  });

  useEffect(() => {
    api.portfolioPreview().then(setPreview);
  }, []);

  const setFilter = (key: string) => (next: string) => setFilters((f) => ({ ...f, [key]: next }));

  return (
    <PageShell
      eyebrow="Module 0 / Lead Portfolio Builder"
      title="Build a high-intent borrower population"
      lede="Start with public-record property and lien data. Layer ownership, market, listing, permit, and lender relationship filters. Every KPI traces to a Cotality source."
      heroRight={<Chip variant="neutral" icon="db">Unity Catalog · metric view</Chip>}
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
              <div className="h-4">Lead Portfolio Builder</div>
              <div className="muted" style={{ fontSize: 12 }}>
                Construct a marketable population from Cotality public records before outreach.
              </div>
            </div>
          </div>
          <Chip variant="neutral" icon="db">mip_demo.gold.lead_population</Chip>
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
            <Button variant="primary" icon="play">Run build</Button>
          </div>

          <div className="kpi-row" style={{ marginTop: 20 }}>
            <KpiCard
              label="Marketable population"
              valueAnimated={preview?.marketable_population ?? 89553}
              delta="+4.8% vs. last run"
              source={DRAWER_SOURCES.population}
              trend={[85200, 85900, 86800, 87400, 88100, 89000, preview?.marketable_population ?? 89553]}
            />
            <KpiCard
              label="Avg. borrower score"
              valueAnimated={preview?.avg_score ?? 81}
              delta="+2"
              source={DRAWER_SOURCES.nbo}
              trend={[74, 76, 77, 78, 79, 80, preview?.avg_score ?? 81]}
            />
            <KpiCard
              label="Cost per contact (est.)"
              valueAnimated={preview?.cost_per_contact ?? 2.18}
              format={(n) => `$${n.toFixed(2)}`}
              delta="-$0.11"
              deltaDir="down"
              source={DRAWER_SOURCES.config}
              trend={[2.42, 2.36, 2.32, 2.28, 2.24, 2.21, preview?.cost_per_contact ?? 2.18]}
            />
            <KpiCard
              label="Projected contact → app"
              valueAnimated={preview?.projected_contact_to_app ?? 9.7}
              format={(n) => n.toFixed(1)}
              unit="%"
              delta="+1.2 pp"
              source={DRAWER_SOURCES.nbo}
              trend={[8.3, 8.6, 8.9, 9.1, 9.3, 9.5, preview?.projected_contact_to_app ?? 9.7]}
            />
          </div>
        </div>
      </div>

      <div style={{ marginTop: 'var(--gap-grid)' }}>
        <ApprovalBanner
          count={preview?.high_intent_leads ?? 12840}
          text="Portfolio build will queue approvals downstream. No outreach is sent until a human approves each recommendation."
          approveLabel="Generate approval-required outreach"
        />
      </div>

      <div style={{ marginTop: 'var(--gap-grid)', display: 'flex', gap: 12 }}>
        <Link to="/segment-intelligence" className="btn btn--primary">
          Next: segment intelligence
          <Icon name="chevright" size={14} />
        </Link>
        <Link to="/lead-queue" className="btn">Jump to lead queue</Link>
      </div>
    </PageShell>
  );
}
