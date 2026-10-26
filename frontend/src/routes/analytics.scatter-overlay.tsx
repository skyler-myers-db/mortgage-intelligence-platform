import { useState, type CSSProperties, type FocusEvent, type KeyboardEvent, type PointerEvent } from 'react';
import { formatCount } from '../lib/formatters';
import type { AnalyticsThresholds } from '../types';
import { pct, type ScatterLayout } from './analytics.lib';

/**
 * The equity scatter's one tooltip idiom and its governed guides
 * (2026-09-21 audit dataviz-07 cut (a), dataviz-06 cut (b), critic-08;
 * deviation:scatter-guides-tooltip). The markers used to carry DOM `title=`
 * text, a third tooltip idiom beside the kit's tip and the browser's native
 * one: they now show the kit's `.analytics-chart__tip` chrome, positioned at
 * the marker under the pointer or holding focus. The tip is aria-hidden and
 * never takes the pointer or focus: every fact it shows is in the marker's
 * accessible name.
 */

/** Where a marker's tip sits (the plot's percent space) and what it says. */
export interface ScatterTipSpot {
  xPct: number;
  yPct: number;
  /** The tip's first line (`.analytics-chart__tip-x`). */
  label: string;
  /** Its second line (`.analytics-chart__tip-y`), when there is one. */
  detail: string | null;
}

/** Past ~60% of the plot width the tip flips left, as the kit's ChartTooltip does. */
const FLIP_AT_PCT = 60;

type MarkerElement = HTMLElement;

export interface ScatterMarkerHandlers {
  onPointerEnter: (event: PointerEvent<MarkerElement>) => void;
  onPointerLeave: () => void;
  onFocus: (event: FocusEvent<MarkerElement>) => void;
  onBlur: () => void;
  onKeyDown: (event: KeyboardEvent<MarkerElement>) => void;
}

/**
 * The tip's state: the marker under the pointer, else the one holding focus.
 * The handlers are one set shared by every marker (they read the marker's
 * `data-scatter-key`), so a pointerleave falls back to the focused marker
 * instead of hiding a tip the keyboard asked for.
 */
export function useScatterTip(spots: ReadonlyMap<string, ScatterTipSpot>): {
  spot: ScatterTipSpot | null;
  handlers: ScatterMarkerHandlers;
} {
  const [hovered, setHovered] = useState<string | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const key = hovered ?? focused;
  const handlers: ScatterMarkerHandlers = {
    onPointerEnter: (event) => setHovered(event.currentTarget.dataset.scatterKey ?? null),
    onPointerLeave: () => setHovered(null),
    onFocus: (event) => setFocused(event.currentTarget.dataset.scatterKey ?? null),
    onBlur: () => setFocused(null),
    onKeyDown: moveScatterMarkerFocus,
  };
  return { spot: key === null ? null : (spots.get(key) ?? null), handlers };
}

/**
 * Roving focus across the markers (a11y-05): the arrow keys, Home and End
 * move between the markers of the marker's own `.analytics-scatter__points`
 * group. The handler sits on each marker, so the group itself is a plain
 * role="group" with no handler.
 */
export function moveScatterMarkerFocus(event: KeyboardEvent<MarkerElement>): void {
  const direction = event.key === 'ArrowRight' || event.key === 'ArrowDown'
    ? 1
    : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
      ? -1
      : 0;
  if (direction === 0 && event.key !== 'Home' && event.key !== 'End') return;
  const group = event.currentTarget.closest('.analytics-scatter__points');
  if (!group) return;
  const markers = [...group.querySelectorAll<HTMLElement>('[data-scatter-marker]')];
  const current = markers.indexOf(event.currentTarget);
  if (current < 0) return;
  event.preventDefault();
  const next = event.key === 'Home'
    ? 0
    : event.key === 'End'
      ? markers.length - 1
      : (current + direction + markers.length) % markers.length;
  markers[next]?.focus();
}

/** The tip, rendered inside `.analytics-scatter__points` (pointer-events: none). */
export function ScatterTip({ spot }: { spot: ScatterTipSpot | null }) {
  if (spot === null) return null;
  const at = { '--hover-x': `${spot.xPct}%`, '--hover-y': `${spot.yPct}%` } as CSSProperties;
  return (
    <span
      className={`analytics-chart__tip analytics-scatter__tip${spot.xPct > FLIP_AT_PCT ? ' analytics-chart__tip--flip' : ''}`}
      style={at}
      aria-hidden="true"
      data-testid="scatter-tip"
    >
      <span className="analytics-chart__tip-x">{spot.label}</span>
      {spot.detail !== null && <span className="analytics-chart__tip-y">{spot.detail}</span>}
    </span>
  );
}

/** The In the Money guides inside the current domain, from the refresh's governed thresholds only. */
export interface ScatterGuides {
  /** Percent of the plot width for the equity guide, or null when outside the domain. */
  xPct: number | null;
  /** Percent of the plot height (top-down) for the spread guide, or null when outside. */
  yPct: number | null;
  equityLabel: string;
  spreadLabel: string;
}

/** Both governed thresholds, or null (no single threshold: no guides). */
export function governedGuideValues(thresholds: AnalyticsThresholds): { equityPct: number; spreadBps: number } | null {
  return thresholds.min_equity_pct !== null && thresholds.min_spread_bps !== null
    ? { equityPct: thresholds.min_equity_pct, spreadBps: thresholds.min_spread_bps }
    : null;
}

export function scatterGuides(thresholds: AnalyticsThresholds, layout: ScatterLayout): ScatterGuides | null {
  const values = governedGuideValues(thresholds);
  if (values === null) return null;
  const inside = (value: number, min: number, range: number) => value >= min && value <= min + range;
  return {
    xPct: inside(values.equityPct, layout.xMin, layout.xRange) ? pct(values.equityPct - layout.xMin, layout.xRange) : null,
    yPct: inside(values.spreadBps, layout.yMin, layout.yRange) ? 100 - pct(values.spreadBps - layout.yMin, layout.yRange) : null,
    equityLabel: `Equity ≥ ${formatCount(values.equityPct)}%`,
    spreadLabel: `Spread ≥ ${formatCount(values.spreadBps)} bps`,
  };
}

/** The guide lines, in the plot svg's 0-100 viewBox. */
export function ScatterGuideLines({ guides }: { guides: ScatterGuides | null }) {
  if (guides === null) return null;
  return (
    <>
      {guides.xPct !== null && (
        <line
          className="analytics-scatter__guide"
          data-guide="equity"
          x1={guides.xPct}
          x2={guides.xPct}
          y1="0"
          y2="100"
          vectorEffect="non-scaling-stroke"
        />
      )}
      {guides.yPct !== null && (
        <line
          className="analytics-scatter__guide"
          data-guide="spread"
          x1="0"
          x2="100"
          y1={guides.yPct}
          y2={guides.yPct}
          vectorEffect="non-scaling-stroke"
        />
      )}
    </>
  );
}

/** The guides' HTML labels (the svg stretches, so text never lives inside it). */
export function ScatterGuideLabels({ guides }: { guides: ScatterGuides | null }) {
  if (guides === null || (guides.xPct === null && guides.yPct === null)) return null;
  return (
    <div className="analytics-scatter__guides" aria-hidden="true">
      {guides.xPct !== null && (
        <span
          className="analytics-scatter__guide-label analytics-scatter__guide-label--equity"
          style={{ '--guide-pos': `${guides.xPct}%` } as CSSProperties}
        >
          {guides.equityLabel}
        </span>
      )}
      {guides.yPct !== null && (
        <span
          className="analytics-scatter__guide-label analytics-scatter__guide-label--spread"
          style={{ '--guide-pos': `${guides.yPct}%` } as CSSProperties}
        >
          {guides.spreadLabel}
        </span>
      )}
    </div>
  );
}

/** The legend line that names the guides, or says why there are none. */
export function guideLegendLine(thresholds: AnalyticsThresholds, notice: string | null): string | null {
  const values = governedGuideValues(thresholds);
  if (values === null) return notice;
  return `Dashed lines: the In the Money thresholds (spread ≥ ${formatCount(values.spreadBps)} bps, equity ≥ ${formatCount(values.equityPct)}%) from this refresh.`;
}
