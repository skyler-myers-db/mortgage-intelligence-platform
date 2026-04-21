import { Link } from 'react-router-dom';
import { PageShell } from '../components/layout/PageShell';
import { KpiCard } from '../components/mortgage/KpiCard';
import { MapPlaceholder } from '../components/mortgage/MapPlaceholder';
import { AgentActivityLog } from '../components/mortgage/AgentActivityLog';
import { Chip, Button } from '../components/Primitives';
import { DRAWER_SOURCES } from '../mocks/demoData';
import { Icon } from '../components/Icon';

const FUTURE_MODULES = [
  { code: 'M1', title: 'Pipeline Optimization', desc: 'Lead → app → approval throughput and stalls.' },
  { code: 'M2', title: 'LO Workbench',          desc: 'Officer assist with explainable next-best-action.' },
  { code: 'M3', title: 'Underwriting Copilot',  desc: 'Condition handling and exception triage.' },
  { code: 'M4', title: 'Risk & Retention',      desc: 'Portfolio-level retention and recapture.' },
];

export default function Home() {
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
      <div className="kpi-row">
        <KpiCard
          label="Marketable population"
          valueAnimated={89553}
          delta="+4.8% vs. last run"
          deltaDir="up"
          source={DRAWER_SOURCES.population}
          trend={[85200, 85900, 86800, 87400, 88100, 89000, 89553]}
        />
        <KpiCard
          label="High-intent leads"
          valueAnimated={12840}
          delta="+18%"
          deltaDir="up"
          source={DRAWER_SOURCES.itm}
          trend={[10200, 10600, 10900, 11400, 12000, 12500, 12840]}
        />
        <KpiCard
          label="Cost per contact (est.)"
          valueAnimated={2.18}
          format={(n) => `$${n.toFixed(2)}`}
          delta="-$0.11"
          deltaDir="down"
          source={DRAWER_SOURCES.config}
          trend={[2.42, 2.36, 2.32, 2.28, 2.24, 2.21, 2.18]}
        />
        <KpiCard
          label="Projected contact → app"
          valueAnimated={9.7}
          format={(n) => n.toFixed(1)}
          unit="%"
          delta="+1.2 pp"
          deltaDir="up"
          source={DRAWER_SOURCES.nbo}
          trend={[8.3, 8.6, 8.9, 9.1, 9.3, 9.5, 9.7]}
        />
      </div>

      <div className="section-hdr">
        <div>
          <div className="eyebrow">Where the opportunity lives</div>
          <div className="h-2">Geography drill-down · county → ZIP → borrower</div>
        </div>
      </div>
      <div className="layoutA-grid">
        <MapPlaceholder />
        <AgentActivityLog />
      </div>

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
