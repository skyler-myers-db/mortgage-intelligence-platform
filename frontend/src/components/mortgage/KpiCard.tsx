import type { ReactNode } from 'react';
import { Icon } from '../Icon';
import { EvidenceChip } from '../Primitives';
import type { DrawerSource } from '../AppContext';

/**
 * KpiCard — prototype `.kpi` BEM: label / value / unit / delta / source.
 * Source renders as a small dashed-border line at the bottom; tapping the
 * evidence chip opens the DataSourceDrawer with the lineage for that KPI.
 */

interface KpiCardProps {
  label: string;
  value: string;
  unit?: string;
  delta?: string;
  deltaDir?: 'up' | 'down' | 'flat';
  source?: DrawerSource;
  spark?: ReactNode;
}

export function KpiCard({ label, value, unit, delta, deltaDir = 'up', source, spark }: KpiCardProps) {
  return (
    <div className="kpi">
      <div className="kpi__label">{label}</div>
      <div className="kpi__value num">
        {value}
        {unit && <span className="kpi__unit">{unit}</span>}
      </div>
      {delta && (
        <div className={`kpi__delta ${deltaDir}`}>
          <Icon name={deltaDir === 'up' ? 'up' : deltaDir === 'down' ? 'down' : 'chevright'} size={11} />
          {delta}
        </div>
      )}
      {spark && <div className="kpi__spark">{spark}</div>}
      {source && (
        <div className="kpi__source">
          <Icon name="db" size={11} />
          <EvidenceChip source={source}>{source.short ?? source.title}</EvidenceChip>
        </div>
      )}
    </div>
  );
}
