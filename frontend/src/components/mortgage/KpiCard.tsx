import type { ReactNode } from 'react';
import { Icon } from '../Icon';
import { EvidenceChip } from '../Primitives';
import type { DrawerSource } from '../AppContext';
import { Sparkline } from './Sparkline';
import { useCountUp } from '../../lib/useCountUp';

/**
 * KpiCard — prototype `.kpi` BEM: label / value / unit / delta / source.
 * Two display paths:
 *   - `value: string` → static rendering (for "$2.18" / "9.7" / non-numeric)
 *   - `valueAnimated: number` + optional `format` → counts up from 0 on mount,
 *     formatter is called per frame so currency/percent/locale formatting
 *     stays outside the hook.
 *
 * `trend` renders a micro-sparkline in the top-right corner using the
 * prototype's `.kpi__spark` slot; color direction honors `deltaDir`.
 */

interface KpiCardProps {
  label: string;
  /** Static pre-formatted value. Used when `valueAnimated` is absent. */
  value?: string;
  /** Target numeric value to animate on mount. Formatter applied per frame. */
  valueAnimated?: number;
  /** Formatter for animated values. Default: .toLocaleString() rounded to int. */
  format?: (n: number) => string;
  unit?: string;
  delta?: string;
  deltaDir?: 'up' | 'down' | 'flat';
  source?: DrawerSource;
  /** Override the rendered spark. Prefer `trend` — only override for custom viz. */
  spark?: ReactNode;
  /** Numeric series (typically 7 points) to render as a sparkline. */
  trend?: number[];
}

const defaultFormat = (n: number): string => Math.round(n).toLocaleString();

export function KpiCard({
  label,
  value,
  valueAnimated,
  format = defaultFormat,
  unit,
  delta,
  deltaDir = 'up',
  source,
  spark,
  trend,
}: KpiCardProps) {
  // Hooks must run unconditionally — pass 0 when valueAnimated is undefined.
  const animated = useCountUp(valueAnimated ?? 0);
  const display = valueAnimated !== undefined ? format(animated) : (value ?? '');

  const sparkNode = spark ?? (trend && trend.length >= 2 ? <Sparkline points={trend} direction={deltaDir} /> : null);

  return (
    <div className="kpi">
      <div className="kpi__label">{label}</div>
      <div className="kpi__value num">
        {display}
        {unit && <span className="kpi__unit">{unit}</span>}
      </div>
      {delta && (
        <div className={`kpi__delta ${deltaDir}`}>
          <Icon name={deltaDir === 'up' ? 'up' : deltaDir === 'down' ? 'down' : 'chevright'} size={11} />
          {delta}
        </div>
      )}
      {sparkNode && <div className="kpi__spark">{sparkNode}</div>}
      {source && (
        <div className="kpi__source">
          <Icon name="db" size={11} />
          <EvidenceChip source={source}>{source.short ?? source.title}</EvidenceChip>
        </div>
      )}
    </div>
  );
}
