import { Link, useLocation } from 'react-router-dom';
import { Icon, type IconName } from '../Icon';

/**
 * Left module rail. Vertical strip, 72px wide. M0 is live today; M1–M4
 * are modules on the published roadmap and render as inactive rail
 * items. The active M0 rail item lights up whenever the user is on any
 * Module 0 route.
 */

interface ModuleItem {
  id: number;
  name: string;
  icon: IconName;
}

const MODULES: ModuleItem[] = [
  { id: 0, name: 'Top-of-Funnel', icon: 'target' },
  { id: 1, name: 'Pipeline',      icon: 'flow' },
  { id: 2, name: 'Pricing',       icon: 'money' },
  { id: 3, name: 'Retention',     icon: 'shield' },
  { id: 4, name: 'Servicing',     icon: 'audit' },
];

export function Rail() {
  const { pathname } = useLocation();
  // All current routes are Module 0.
  const activeModuleId = 0;
  const isM0 = activeModuleId === 0 && pathname !== '/__unused';
  return (
    <nav className="rail" aria-label="Modules">
      <Link to="/" className="rail__brand" title="Entrada Mortgage Intelligence Platform"><span>ENT</span></Link>
      {MODULES.map((m) => {
        const active = m.id === 0 ? isM0 : false;
        const cls = `rail__item ${active ? 'is-active' : ''}`;
        // M0 links home; M1+ point at the roadmap panel on Admin.
        const href = m.id === 0 ? '/' : '/admin-config';
        return (
          <Link
            key={m.id}
            to={href}
            className={cls}
            title={`Module ${m.id}: ${m.name}${m.id === 0 ? '' : ' — on roadmap'}`}
            aria-current={active ? 'page' : undefined}
          >
            <Icon name={m.icon} size={18} className="ico" />
            <span className="mod">M{m.id}</span>
          </Link>
        );
      })}
      <div className="rail__spacer" />
      <Link to="/admin-config" className="rail__item" title="Admin / settings">
        <Icon name="settings" size={16} />
      </Link>
    </nav>
  );
}
