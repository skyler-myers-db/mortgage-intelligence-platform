import { Icon } from '../components/Icon';
import {
  leadTableHideableColumns,
  leadTableHideToken,
  type LeadTableHiddenColumns,
  type LeadTableHideToken,
  type LeadTableView,
} from '../components/mortgage/LeadTable.columns';
import { lazyModule, useLazyModule } from '../components/mortgage/useLazyModule';

/**
 * The HIDE pill's slot in the ranked table's header (tables-05 step 2;
 * deviation:lead-table-hide-columns). The control (LeadTableHideControl and
 * the shared MultiFilterSelect) loads right after the queue mounts, off the
 * route's closure (precedent: the address lookup panel); until it lands, a
 * same-size, inert, aria-hidden pill holds its place so the header never
 * shifts. Loading it reads nothing.
 */
const HIDE_CONTROL = lazyModule(() => import('../components/mortgage/LeadTableHideControl'));

/** The pill's value: No columns, the one hidden column's label, or "N columns". */
function hideValue(view: LeadTableView, hidden: LeadTableHiddenColumns): string {
  const labels = leadTableHideableColumns(view)
    .filter((column) => {
      const token = leadTableHideToken(column.key);
      return token !== null && hidden.has(token);
    })
    .map((column) => column.label);
  const [onlyLabel] = labels;
  if (onlyLabel === undefined) return 'No columns';
  return labels.length === 1 ? onlyLabel : `${labels.length} columns`;
}

export function LeadQueueHideSlot({
  view,
  hidden,
  onChange,
}: {
  view: LeadTableView;
  hidden: LeadTableHiddenColumns;
  onChange: (next: LeadTableHideToken[]) => void;
}) {
  const control = useLazyModule(HIDE_CONTROL, true).module;
  if (control) return <control.LeadTableHideControl view={view} hidden={hidden} onChange={onChange} />;
  return (
    <span className="filter-root" aria-hidden="true" data-testid="lead-table-hide-placeholder">
      <span className={hidden.size > 0 ? 'filter is-active' : 'filter'}>
        <span className="filter__label">HIDE</span>
        <span className="filter__value">{hideValue(view, hidden)}</span>
        <Icon name="chevdown" size={11} />
      </span>
    </span>
  );
}
