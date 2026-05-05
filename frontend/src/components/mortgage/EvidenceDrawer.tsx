import { Fragment, useEffect, useRef } from 'react';
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
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  const lastFocusedRef = useRef<HTMLElement | null>(null);

  // A11y: ESC closes; focus lands on the close button on open and returns
  // to the element that triggered the open on close. Prevents sighted
  // keyboard users from being stranded in the dialog.
  useEffect(() => {
    if (open) {
      lastFocusedRef.current = document.activeElement as HTMLElement | null;
      // Defer to next frame so the element is visible + focusable.
      queueMicrotask(() => closeBtnRef.current?.focus());
      const onKey = (e: KeyboardEvent) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          setDrawer(null);
        }
      };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }
    // When drawer closes, restore focus to whatever opened it.
    if (lastFocusedRef.current && typeof lastFocusedRef.current.focus === 'function') {
      lastFocusedRef.current.focus();
      lastFocusedRef.current = null;
    }
    return undefined;
  }, [open, setDrawer]);

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
        aria-modal="true"
        aria-label="Data source and lineage"
        aria-hidden={!open}
      >
        <div className="drawer__hdr">
          <div className="drawer__source-icon">
            <Icon name="db" size={16} />
          </div>
          <div>
            <div className="drawer__title">Data source & lineage</div>
            <div className="drawer__subtitle">{d?.title ?? '—'}</div>
          </div>
          <button ref={closeBtnRef} className="drawer__close" onClick={() => setDrawer(null)} aria-label="Close drawer" type="button">
            <Icon name="close" size={14} />
          </button>
        </div>
        <div className="drawer__body">
          <div
            aria-label="Source freshness legend"
            title="Fresh: updated within 7 days · Aging: 7–30 days · Stale: over 30 days or unknown"
            className="freshness-legend mb-3"
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
          {d?.description && <p className="body flush">{d.description}</p>}
          {d?.lineage && d.lineage.length > 0 && (
            <>
              <div className="eyebrow mt-4 mb-2">Lineage</div>
              {d.lineage.map((n, i) => (
                <Fragment key={`${n.name}-${i}`}>
                  <div className="lineage-node">
                    <div className="lineage-node__label">{n.layer}</div>
                    <div className="lineage-node__name">{n.name}</div>
                    {n.meta && (
                      <div className="lineage-node__meta">{n.meta}</div>
                    )}
                  </div>
                  {d.lineage && i < d.lineage.length - 1 && <div className="lineage-arrow">↓</div>}
                </Fragment>
              ))}
            </>
          )}
          {d?.signals && d.signals.length > 0 && (
            <>
              <div className="eyebrow mt-5 mb-2">Raw signals</div>
              {d.signals.map((s, i) => (
                <div
                  key={`${s.label}-${i}`}
                  className="lineage-node lineage-node--signal"
                >
                  <div>
                    <div className="lineage-node__label">{s.label}</div>
                    <div className="lineage-node__name">{s.source}</div>
                  </div>
                  <div className="mono num lineage-node__value">{s.value}</div>
                </div>
              ))}
            </>
          )}
          {d?.updatedAt && (
            <div className="drawer__updated">
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
