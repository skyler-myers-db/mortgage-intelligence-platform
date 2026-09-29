/**
 * The kit's one tooltip (2026-09-21 audit dataviz-07 step 2): a crosshair,
 * a dot and a tip in the plot's percent space, on the existing
 * `.analytics-chart__crosshair / __hover-dot / __tip(--flip) / __tip-x /
 * __tip-y` chrome. It shows the cursor's point whether a pointer or the
 * keyboard set it. The whole layer is aria-hidden: the plot's description,
 * the table view and the frame's live region carry the same values to
 * assistive technology.
 */
import type { CSSProperties, ReactNode } from 'react';
import type { ChartCursorSurfaceProps } from './useChartCursor';

export interface ChartTooltipPoint {
  /** Percent of the plot width. */
  x: number;
  /** Percent of the plot height, top-down. */
  y: number;
  label: ReactNode;
  value: ReactNode;
}

/** Past ~60% of the plot width the tip would overflow the surface, so it flips left. */
const FLIP_AT_PCT = 60;

export interface ChartTooltipProps {
  point: ChartTooltipPoint | null;
  /** The pointer surface's handlers (the layer spans the plot). */
  surfaceProps: ChartCursorSurfaceProps;
}

/** A compiled shell over a plain render (see "Compiled shells" in CountChart.tsx). */
export function ChartTooltip(props: ChartTooltipProps) {
  return chartTooltip(props);
}

function chartTooltip({ point, surfaceProps }: ChartTooltipProps) {
  const at = point ? ({ '--hover-x': `${point.x}%`, '--hover-y': `${point.y}%` } as CSSProperties) : undefined;
  return (
    <div className="analytics-chart__hover" aria-hidden="true" {...surfaceProps}>
      {point && (
        <>
          <span className="analytics-chart__crosshair" style={at} />
          <span className="analytics-chart__hover-dot" style={at} />
          <span className={`analytics-chart__tip${point.x > FLIP_AT_PCT ? ' analytics-chart__tip--flip' : ''}`} style={at}>
            <span className="analytics-chart__tip-x">{point.label}</span>
            <span className="analytics-chart__tip-y">{point.value}</span>
          </span>
        </>
      )}
    </div>
  );
}
