import { createPortal } from 'react-dom';
import type { HoverState } from './USChoroplethMap.utils';

interface USChoroplethMapTooltipProps {
  hover: HoverState;
  activeSegNames: Set<string> | null;
}

export function USChoroplethMapTooltip({ hover, activeSegNames }: USChoroplethMapTooltipProps) {
  return createPortal(
    <div
      className="map-tip"
      style={{
        position: 'fixed',
        left: Math.max(160, Math.min(window.innerWidth - 160, hover.x)),
        top: hover.y - 4,
      }}
    >
      <div className="map-tip__name">{hover.name}</div>
      <div className="map-tip__kpis">
        <div className="map-tip__kpi">
          <div className="map-tip__kpi-label">Marketable borrowers</div>
          <div className="map-tip__kpi-value">
            {hover.count !== null ? hover.count.toLocaleString() : '—'}
          </div>
        </div>
        <div className="map-tip__kpi">
          <div className="map-tip__kpi-label">Avg. opportunity score</div>
          <div className="map-tip__kpi-value">
            {hover.avgScore !== null ? hover.avgScore : '—'}
          </div>
        </div>
      </div>
      {hover.topSegment && (
        <div className="map-tip__seg">
          <span className="map-tip__seg-label">Top segment</span>
          <span className="map-tip__seg-value">{hover.topSegment}</span>
        </div>
      )}
      {activeSegNames !== null && (
        <div className="map-tip__row map-tip__row--compact map-tip__row--muted">
          <span>Filter</span>
          <span className="v map-tip__value--small">
            filtered by {Array.from(activeSegNames).join(', ')}
          </span>
        </div>
      )}
      <div className="map-tip__row map-tip__row--compact">
        <span>Source</span>
        <span className="v mono map-tip__value--small">
          {hover.sourceHint ?? 'mip.gold'}
        </span>
      </div>
    </div>,
    document.body,
  );
}
