import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { PageShell } from '../components/layout/PageShell';
import { KpiCard } from '../components/mortgage/KpiCard';
import { MapPlaceholder } from '../components/mortgage/MapPlaceholder';
import { AgentActivityLog } from '../components/mortgage/AgentActivityLog';
import { Chip, Button } from '../components/Primitives';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { Icon } from '../components/Icon';
import { Reveal } from '../components/fx/Reveal';
import { api } from '../lib/api';
import type { PortfolioPreview } from '../types';

const FUTURE_MODULES = [
  { code: 'M1', title: 'Pipeline Optimization', desc: 'Lead → app → approval throughput and stalls.' },
  { code: 'M2', title: 'LO Workbench',          desc: 'Officer assist with explainable next-best-action.' },
  { code: 'M3', title: 'Underwriting Copilot',  desc: 'Condition handling and exception triage.' },
  { code: 'M4', title: 'Risk & Retention',      desc: 'Portfolio-level retention and recapture.' },
];

export default function Home() {
  // Home KPIs read straight from /api/portfolio/preview. While the request is
  // in flight we show an em-dash placeholder rather than design-time numbers
  // so the surface never presents a plausible-but-fake value. The KpiCard
  // component interprets a null `valueAnimated` as "render em-dash".
  const [preview, setPreview] = useState<PortfolioPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .portfolioPreview()
      .then((p) => {
        if (!cancelled) {
          setPreview(p);
          setPreviewError(null);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setPreview(null);
        setPreviewError(
          err instanceof Error ? err.message : "Couldn't reach /api/portfolio/preview.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const queued = preview?.high_intent_leads ?? null;

  return (
    <PageShell
      eyebrow="Module 0 · Top-of-Funnel Lead Generation & Borrower Segmentation"
      title="Who should we contact, why now, and with what offer?"
      lede="Grounded on Cotality public records, liens, listings, permits, AVM, and mortgage market data. Every recommendation is traceable, every score has a rationale, and nothing is sent without human approval."
      heroRight={
        <>
          <Chip variant="neutral" icon="db">Refreshed 06:12 UTC · Delta Share</Chip>
          <Link to="/portfolio-builder" className="btn btn--primary">
            Start: build a portfolio
            <Icon name="chevright" size={14} />
          </Link>
        </>
      }
    >
      {previewError && (
        <div
          role="alert"
          style={{
            marginBottom: 'var(--gap-grid)',
            padding: '10px 12px',
            border: '1px solid var(--signal-danger)',
            borderRadius: 'var(--r-md)',
            color: 'var(--signal-danger)',
            fontSize: 12,
          }}
        >
          Couldn&apos;t load portfolio KPIs: {previewError}
        </div>
      )}
      <div className="kpi-row">
        <KpiCard
          label="Marketable population"
          valueAnimated={preview?.marketable_population ?? null}
          source={DRAWER_SOURCES.population}
        />
        <KpiCard
          label="High-intent leads"
          valueAnimated={preview?.high_intent_leads ?? null}
          source={DRAWER_SOURCES.itm}
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

      <div
        className="approval"
        role="region"
        aria-label="Human approval required before outreach"
        style={{ marginTop: 'var(--gap-grid)' }}
      >
        <div className="approval__ico"><Icon name="shield" size={16} /></div>
        <div className="approval__body">
          <div className="approval__title">Review approval required before outreach</div>
          <div className="approval__sub">
            {queued !== null
              ? `${queued.toLocaleString()} borrowers queued. Nothing is sent until an officer approves each draft.`
              : 'Nothing is sent until an officer approves each draft.'}
          </div>
        </div>
      </div>

      <div className="section-hdr">
        <div>
          <div className="eyebrow">Where the opportunity lives</div>
          <div className="h-2">Geography drill-down · county → ZIP → borrower</div>
        </div>
      </div>
      <div className="layoutA-grid">
        <MapPlaceholder />
        <Reveal>
          <AgentActivityLog />
        </Reveal>
      </div>

      <Reveal>
        <div className="section-hdr">
          <div>
            <div className="eyebrow">Future modules</div>
            <div className="h-2">One spine, four extensions</div>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 'var(--gap-grid)' }}>
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
    </PageShell>
  );
}
