import { Link, useLocation } from 'react-router-dom';
import { Icon, type IconName } from '../Icon';
import { EntradaMark } from '../brand/Entrada';

/**
 * Left module rail. Vertical strip, 72px wide. M0 ships today; M1–M4
 * are modules on the published roadmap and render as inactive, non-
 * interactive rail items (persona review 2026-04-22 blocker #5: M1-M4
 * previously linked to /admin-config which was an unrelated dead link).
 * The active M0 rail item lights up whenever the user is on any
 * Module 0 route.
 *
 * Copy note (2026-04-23 hole-finder round 2): avoid the word "live" on
 * the rail since Module 0 data refreshes nightly via Delta Share, not
 * in real time. "Ships today" keeps the roadmap cue without implying a
 * streaming-data posture.
 */

interface ModuleItem {
  id: number;
  name: string;
  icon: IconName;
  desc: string;
}

const MODULES: ModuleItem[] = [
  { id: 0, name: 'Top-of-Funnel',        icon: 'target', desc: 'Lead generation + borrower segmentation (ships today).' },
  { id: 1, name: 'Pipeline Optimization', icon: 'flow',   desc: 'Lead → app → approval throughput and stalls (on roadmap).' },
  { id: 2, name: 'LO Workbench',          icon: 'money',  desc: 'Officer assist with explainable next-best-action (on roadmap).' },
  { id: 3, name: 'Underwriting Copilot',  icon: 'shield', desc: 'Condition handling and exception triage (on roadmap).' },
  { id: 4, name: 'Risk & Retention',      icon: 'audit',  desc: 'Portfolio-level retention and recapture (on roadmap).' },
];

export function Rail() {
  const { pathname } = useLocation();
  // All current routes are Module 0.
  const activeModuleId = 0;
  const isM0 = activeModuleId === 0 && pathname !== '/__unused';
  return (
    <nav className="rail" aria-label="Primary navigation">
      <Link to="/" className="rail__brand" title="Entrada — Mortgage Intelligence Platform" aria-label="Entrada home">
        <EntradaMark size={32} />
      </Link>
      {MODULES.map((m) => {
        if (m.id === 0) {
          const active = isM0;
          const cls = `rail__item ${active ? 'is-active' : ''}`;
          return (
            <Link
              key={m.id}
              to="/"
              className={cls}
              title={`Module ${m.id}: ${m.name} — ${m.desc}`}
              aria-current={active ? 'page' : undefined}
            >
              <Icon name={m.icon} size={18} className="ico" />
              <span className="mod">M{m.id}</span>
            </Link>
          );
        }
        // M1-M4: inactive, non-interactive. Render as <span> with
        // tooltip + reduced-opacity visual cue. Not navigable.
        return (
          <span
            key={m.id}
            className="rail__item rail__item--disabled"
            role="presentation"
            aria-disabled="true"
            title={`Module ${m.id}: ${m.name} — ${m.desc}`}
          >
            <Icon name={m.icon} size={18} className="ico" />
            <span className="mod">M{m.id}</span>
          </span>
        );
      })}
      <div className="rail__spacer" />
      <Link to="/admin-config" className="rail__item" title="Admin / settings">
        <Icon name="settings" size={16} />
      </Link>
    </nav>
  );
}
