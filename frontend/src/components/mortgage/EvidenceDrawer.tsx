import { Fragment } from 'react';
import { useApp } from '../AppContext';
import { Icon } from '../Icon';

/**
 * Data source / evidence drawer — `.drawer` BEM from the prototype.
 * Slides in from the right with scrim, shows lineage nodes + signal rows
 * for whatever drawer source is currently set on AppContext.
 *
 * Any page can open it via `useApp().setDrawer(SOURCE)`.
 */

export function EvidenceDrawer() {
  const { drawer, setDrawer } = useApp();
  const open = !!drawer;
  const d = drawer;

  return (
    <>
      <div
        className={`drawer-scrim ${open ? 'is-open' : ''}`}
        onClick={() => setDrawer(null)}
        aria-hidden={!open}
      />
      <aside
        className={`drawer ${open ? 'is-open' : ''}`}
        role="dialog"
        aria-label="Data source and lineage"
        aria-hidden={!open}
      >
        <div className="drawer__hdr">
          <div
            style={{
              display: 'grid',
              placeItems: 'center',
              width: 32,
              height: 32,
              background: 'var(--accent-soft)',
              color: 'var(--accent)',
              borderRadius: 8,
            }}
          >
            <Icon name="db" size={16} />
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Data source & lineage</div>
            <div style={{ fontSize: 11, color: 'var(--text-3)' }}>{d?.title ?? '—'}</div>
          </div>
          <button className="drawer__close" onClick={() => setDrawer(null)} aria-label="Close drawer" type="button">
            <Icon name="close" size={14} />
          </button>
        </div>
        <div className="drawer__body">
          <div
            className="freshness-legend"
            aria-label="Source freshness legend"
            title="Fresh: updated within 7 days · Aging: 7–30 days · Stale: over 30 days or unknown"
            style={{ marginBottom: 10 }}
          >
            <span className="freshness-legend__item">
              <span className="freshness-legend__dot freshness-legend__dot--fresh" aria-hidden="true" />
              Fresh
            </span>
            <span className="freshness-legend__item">
              <span className="freshness-legend__dot freshness-legend__dot--aging" aria-hidden="true" />
              Aging
            </span>
            <span className="freshness-legend__item">
              <span className="freshness-legend__dot freshness-legend__dot--stale" aria-hidden="true" />
              Stale
            </span>
          </div>
          {d?.description && <p className="body" style={{ marginTop: 0 }}>{d.description}</p>}
          {d?.lineage && d.lineage.length > 0 && (
            <>
              <div className="eyebrow" style={{ marginTop: 16, marginBottom: 8 }}>Lineage</div>
              {d.lineage.map((n, i) => (
                <Fragment key={`${n.name}-${i}`}>
                  <div className="lineage-node">
                    <div className="lineage-node__label">{n.layer}</div>
                    <div className="lineage-node__name">{n.name}</div>
                    {n.meta && (
                      <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 4 }}>{n.meta}</div>
                    )}
                  </div>
                  {d.lineage && i < d.lineage.length - 1 && <div className="lineage-arrow">↓</div>}
                </Fragment>
              ))}
            </>
          )}
          {d?.signals && d.signals.length > 0 && (
            <>
              <div className="eyebrow" style={{ marginTop: 20, marginBottom: 8 }}>Raw signals</div>
              {d.signals.map((s, i) => (
                <div
                  key={`${s.label}-${i}`}
                  className="lineage-node"
                  style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}
                >
                  <div>
                    <div className="lineage-node__label">{s.label}</div>
                    <div className="lineage-node__name">{s.source}</div>
                  </div>
                  <div className="mono num" style={{ color: 'var(--text-1)', fontSize: 13 }}>{s.value}</div>
                </div>
              ))}
            </>
          )}
          {d?.updatedAt && (
            <div style={{ marginTop: 16, fontSize: 11, color: 'var(--text-3)' }}>
              Last refresh: {d.updatedAt} · via Delta Share
            </div>
          )}
          {!d && (
            <p className="muted">Tap any evidence chip or KPI source line to inspect the lineage.</p>
          )}
        </div>
      </aside>
    </>
  );
}
