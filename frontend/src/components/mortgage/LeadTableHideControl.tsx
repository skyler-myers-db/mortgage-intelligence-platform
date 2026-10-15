import { MultiFilterSelect, type MultiFilterSelectOption } from '../ui/MultiFilterSelect';
import {
  leadTableHideableColumns,
  leadTableHideToken,
  type LeadTableHiddenColumns,
  type LeadTableHideToken,
  type LeadTableView,
} from './LeadTable.columns';

/**
 * The HIDE pill (tables-05 step 2, W5e). deviation:lead-table-hide-columns
 * The prototype's `.filter` pill family (design_files/index.html:822-838)
 * as a multi-select (components/ui/MultiFilterSelect): the current view's
 * hideable columns by their header labels, "No columns" when none is
 * hidden. The selection, row expand, Borrower, Primary offer (it carries the
 * rules evidence chip), Score, Signal and Approval columns are never
 * offered (LeadTable.columns.ts). The route owns the value (`?hide=`,
 * lead-queue.columns.ts) and renders this through LeadTableHeaderSlotContext;
 * it loads right after the queue (routes/lead-queue.hideSlot.tsx), so neither
 * it nor MultiFilterSelect rides the route's or the LeadTable chunk. Hiding a
 * column reads nothing and writes no audit row; the CSV export keeps every
 * column.
 */
const OPTIONS: Partial<Record<LeadTableView, ReadonlyArray<MultiFilterSelectOption<LeadTableHideToken>>>> = {};

function hideOptions(view: LeadTableView): ReadonlyArray<MultiFilterSelectOption<LeadTableHideToken>> {
  OPTIONS[view] ??= leadTableHideableColumns(view).flatMap((column) => {
    const token = leadTableHideToken(column.key);
    return token === null ? [] : [{ label: column.label, value: token }];
  });
  return OPTIONS[view];
}

const columnsLabel = (count: number) => `${count} columns`;

export function LeadTableHideControl({
  view,
  hidden,
  onChange,
}: {
  view: LeadTableView;
  hidden: LeadTableHiddenColumns;
  onChange: (next: LeadTableHideToken[]) => void;
}) {
  const options = hideOptions(view);
  return (
    <MultiFilterSelect
      label="HIDE"
      allLabel="No columns"
      selected={options.filter((option) => hidden.has(option.value)).map((option) => option.value)}
      options={options}
      onChange={onChange}
      formatCount={columnsLabel}
    />
  );
}
