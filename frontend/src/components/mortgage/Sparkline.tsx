/**
 * Sparkline — zero-dep inline SVG mini line chart. Sized for the `.kpi__spark`
 * slot (bottom-right of a KPI card, per the prototype). Stroke uses the live
 * --accent token; a faint area fill sits below. Direction (up/down/flat) is
 * derived from first vs. last point so the line's color signal matches the
 * KPI's delta chip.
 */

interface SparklineProps {
  points: number[];
  width?: number;
  height?: number;
  direction?: 'up' | 'down' | 'flat';
  /**
   * When true, the stroke draws in left-to-right once (the KPI one-time
   * entrance, re-audit #4 #2). Pure CSS via stroke-dasharray/offset; honors
   * prefers-reduced-motion. Off by default so non-KPI usages are unchanged.
   */
  drawIn?: boolean;
}

export function Sparkline({ points, width = 64, height = 20, direction, drawIn = false }: SparklineProps) {
  if (!points || points.length < 2) return null;

  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  // Leave 1px padding so the stroke isn't clipped.
  const innerH = height - 2;
  const innerW = width - 2;
  const stepX = innerW / (points.length - 1);

  const path = points
    .map((p, i) => {
      const x = 1 + i * stepX;
      const y = 1 + innerH - ((p - min) / span) * innerH;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');

  // Closed area path (down to baseline, back to start) for the soft fill.
  const areaPath = `${path} L${(1 + (points.length - 1) * stepX).toFixed(2)} ${(height - 1).toFixed(2)} L1 ${(height - 1).toFixed(2)} Z`;

  const dir = direction ?? (points[points.length - 1] > points[0] ? 'up' : points[points.length - 1] < points[0] ? 'down' : 'flat');
  const stroke =
    dir === 'down' ? 'var(--signal-danger)' : dir === 'flat' ? 'var(--text-3)' : 'var(--accent)';

  const gradientId = `spark-fill-${points.slice(0, 3).join('-').replace(/\./g, '_')}-${dir}`;

  return (
    <svg
      className="spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradientId})`} />
      <path
        className={drawIn ? 'spark__line spark__line--draw' : 'spark__line'}
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
