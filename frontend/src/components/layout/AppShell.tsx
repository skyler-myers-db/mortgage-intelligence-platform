import { NavLink } from 'react-router-dom';
import { PropsWithChildren, useState } from 'react';
import { EvidenceDrawer } from '../mortgage/EvidenceDrawer';

const modules = [
  ['M0', 'Top-of-Funnel', '/'],
  ['M1', 'Pipeline', '/'],
  ['M2', 'LO Workbench', '/'],
  ['M3', 'Underwriting', '/'],
  ['M4', 'Risk/Retention', '/']
];

export function AppShell({ children }: PropsWithChildren) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  return (
    <div className="app-shell">
      <aside className="module-rail">
        <div className="brand">ENT</div>
        {modules.map(([code, title, href]) => (
          <NavLink key={code} to={href} title={title} className={({ isActive }) => `rail-link ${code === 'M0' && isActive ? 'active' : ''}`}>
            {code}
          </NavLink>
        ))}
      </aside>
      <header className="topbar">
        <span className="mono muted">WORKSPACE / entrada-mortgage-ai /</span>
        <strong>Module 0: Top-of-Funnel</strong>
        <div className="spacer" />
        <span className="pill">🏦 Summit Mortgage</span>
        <span className="pill">demo.sandbox</span>
        <span className="pill">● serverless-xl</span>
        <button className="button ghost" onClick={() => setDrawerOpen(true)}>Evidence</button>
      </header>
      <main className="main">{children}</main>
      <EvidenceDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </div>
  );
}
