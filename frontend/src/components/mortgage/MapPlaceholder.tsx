export function MapPlaceholder() {
  return (
    <div className="map-placeholder">
      <div style={{ textAlign: 'center' }}>
        <div className="eyebrow">Geography drill-down</div>
        <h2 className="h2">County → ZIP → borrower</h2>
        <p className="muted">MapLibre/GeoJSON production path; booth-safe SVG/mock fallback.</p>
        <span className="chip">Atlanta MSA · 30305 · 30309 · 30324</span>
      </div>
    </div>
  );
}
