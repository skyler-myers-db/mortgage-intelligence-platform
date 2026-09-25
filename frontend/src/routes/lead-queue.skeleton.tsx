import { Icon } from '../components/Icon';
import { leadTableColumns, type LeadTableColumnKey } from '../components/mortgage/LeadTable.columns';
import { useIsOnline } from '../lib/connectivity';
import { SurfaceTitle } from '../components/ui/SurfaceTitle';
import './lead-queue.skeleton.css';

/**
 * The Lead Queue's table slot while no payload exists (first load and
 * warm-up; see lead-queue.tsx).
 *
 * Shaped like the table it stands in for (audit 2026-09-21 `states-10`): it
 * renders the ranked-borrower table's own markup (`.tbl.lead-table__table`),
 * with the Default view's `<colgroup>` and header labels from the same typed
 * column model (LeadTable.columns.ts), so every column sits where the real one
 * will and every row takes the real row height (`.tbl td` is `--row-h`, the
 * one-line rows' height). The old placeholder was six 14px grid rows beside
 * ~44px real rows, so the page jumped when the queue landed.
 *
 * While the browser is offline the query is paused, not loading: the copy
 * says so and the shimmer stops (the `.skeleton` rule keys off the
 * `data-connection` attribute HealthProvider sets).
 */

/** Rows reserved: about what the queue fills above the fold at 1440x900. */
export const LEAD_QUEUE_SKELETON_ROWS = 10;

const COLUMNS = leadTableColumns('default');

/** Placeholder per column, shaped like that column's real content. */
const CELL_SHAPE: Partial<Record<LeadTableColumnKey, string>> = {
  select: 'lead-queue-skeleton__box',
  borrower: 'lead-queue-skeleton__bar',
  location: 'lead-queue-skeleton__bar',
  segments: 'lead-queue-skeleton__bar lead-queue-skeleton__bar--pill',
  equity: 'lead-queue-skeleton__bar lead-queue-skeleton__bar--num',
  rate: 'lead-queue-skeleton__bar lead-queue-skeleton__bar--num',
  offer: 'lead-queue-skeleton__bar',
  score: 'lead-queue-skeleton__bar lead-queue-skeleton__bar--num',
  confidence: 'lead-queue-skeleton__bar lead-queue-skeleton__bar--pill',
  status: 'lead-queue-skeleton__bar lead-queue-skeleton__bar--pill',
  approval: 'lead-queue-skeleton__button',
};

/** The `<td>` classes LeadTableRow puts on these columns (padding, alignment, the Approval pin). */
const CELL_CLASS: Partial<Record<LeadTableColumnKey, string>> = {
  select: 'tbl-cell--select',
  equity: 'tbl-cell--right',
  rate: 'tbl-cell--right',
  score: 'tbl-cell--right',
  approval: 'tbl-cell--approval',
};

const ROW_KEYS = Array.from({ length: LEAD_QUEUE_SKELETON_ROWS }, (_, index) => `row-${index}`);

export function LeadQueueTableSkeleton() {
  const online = useIsOnline();
  return (
    <div className="surface lead-queue-skeleton mb-grid" aria-busy={online} role="status">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon" aria-hidden="true">
            <Icon name="user" size={14} />
          </div>
          <div>
            <SurfaceTitle>{online ? 'Loading ranked borrowers' : 'Waiting for a connection'}</SurfaceTitle>
            <div className="mt-2">
              <span className="muted fs-12">
                {online
                  ? 'Fetching the current queue with the selected filters.'
                  : 'The queue loads as soon as you are back online.'}
              </span>
            </div>
          </div>
        </div>
        <span className="skeleton lead-queue-skeleton__action" aria-hidden="true" />
      </div>
      <table
        className="tbl lead-table__table lead-table__table--default lead-queue-skeleton__table"
        aria-hidden="true"
      >
        <colgroup>
          {COLUMNS.map((column) => <col key={column.key} className={column.colClass} />)}
        </colgroup>
        <thead>
          <tr>
            {COLUMNS.map((column) => <th key={column.key} className={column.thClass}>{column.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {ROW_KEYS.map((rowKey) => (
            <tr key={rowKey}>
              {COLUMNS.map((column) => {
                const shape = CELL_SHAPE[column.key];
                return (
                  <td key={column.key} className={CELL_CLASS[column.key]}>
                    {shape && <span className={`skeleton ${shape}`} />}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
