import { FilterSelect } from '../ui/FilterSelect';
import {
  LEAD_TABLE_VIEW_LABELS,
  LEAD_TABLE_VIEWS,
  type LeadTableView,
} from './LeadTable.columns';

const OPTIONS = LEAD_TABLE_VIEWS.map((view) => LEAD_TABLE_VIEW_LABELS[view]);

/**
 * Column-preset picker (audit tables-05, S part): Default is the prototype's
 * columns plus the merged Status cell; Sales ops adds Relationship, Assigned
 * to, Outreach and Last touch back as separate columns. The route owns the
 * value and persists it in `?view=` (lead-queue.filters.ts). It is the
 * prototype's `.filter` pill (design_files/index.html:826), not a new widget;
 * there is no drag-resize or column reorder.
 */
export function LeadTableViewControl({
  view,
  onChange,
}: {
  view: LeadTableView;
  onChange: (view: LeadTableView) => void;
}) {
  return (
    <FilterSelect
      label="VIEW"
      value={LEAD_TABLE_VIEW_LABELS[view]}
      options={OPTIONS}
      onChange={(label) => {
        const next = LEAD_TABLE_VIEWS.find((candidate) => LEAD_TABLE_VIEW_LABELS[candidate] === label);
        if (next && next !== view) onChange(next);
      }}
    />
  );
}
