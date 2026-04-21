import { Icon } from '../Icon';
import { Chip } from '../Primitives';

/**
 * MapPlaceholder — visual upgrade of the geography panel. Matches the
 * prototype's `.map-wrap` gradient backdrop, top-left breadcrumb area, and
 * top-right drill-hint chip. Real interactive MapLibre / county-drill
 * implementation is a later slice; this renders the chassis so the page
 * reads as designed.
 */

export function MapPlaceholder({ height = 420 }: { height?: number }) {
  return (
    <div className="map-wrap" style={{ height }}>
      <div
        style={{
          position: 'absolute',
          top: 12,
          left: 14,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          zIndex: 2,
        }}
      >
        <div className="eyebrow">Geography drill-down</div>
        <div className="topbar__crumbs" style={{ fontSize: 12 }}>
          <span className="filter" style={{ padding: '3px 8px' }}>
            <span className="filter__value">US</span>
          </span>
          <Icon name="chevright" size={11} />
          <span className="filter" style={{ padding: '3px 8px' }}>
            <span className="filter__value">Georgia</span>
          </span>
          <Icon name="chevright" size={11} />
          <span className="filter is-active" style={{ padding: '3px 8px' }}>
            <span className="filter__value">Atlanta MSA</span>
          </span>
        </div>
      </div>
      <div style={{ position: 'absolute', top: 12, right: 14, zIndex: 2 }}>
        <Chip variant="neutral" icon="pin">Click a ZIP to drill to borrower</Chip>
      </div>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'grid',
          placeItems: 'center',
          padding: 32,
          textAlign: 'center',
        }}
      >
        <div>
          <div
            style={{
              width: 48,
              height: 48,
              margin: '0 auto 12px',
              borderRadius: 12,
              background: 'var(--accent-soft)',
              color: 'var(--accent)',
              display: 'grid',
              placeItems: 'center',
            }}
          >
            <Icon name="map" size={22} />
          </div>
          <div className="h-3">County → ZIP → borrower</div>
          <p className="muted" style={{ maxWidth: 420, margin: '8px auto 0' }}>
            Choropleth of marketable population ranked by opportunity score. Atlanta MSA shown as the
            default demo geography. Interactive MapLibre drill lands in the next slice.
          </p>
          <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            <span className="chip">30305</span>
            <span className="chip">30309</span>
            <span className="chip">30324</span>
            <span className="chip">30339</span>
          </div>
        </div>
      </div>
      <div className="map-legend">
        <div>Borrowers in selection <span style={{ color: 'var(--text-1)', fontFamily: 'var(--font-mono)', marginLeft: 6 }}>89,553</span></div>
        <div className="map-legend__bar">
          <span style={{ background: 'var(--bg-3)' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 15%, var(--bg-3))' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 30%, var(--bg-3))' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 50%, var(--bg-3))' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 70%, var(--bg-3))' }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10 }}>
          <span>Lower</span>
          <span>Higher</span>
        </div>
      </div>
    </div>
  );
}
