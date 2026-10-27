/**
 * The expanded row's slot for its lazy body (LeadRowExpanded: the Borrower
 * 360 preview and the workflow strip). That body is its own chunk, cut out
 * of the shared leadsQuery (LeadTable) chunk to fund tail T1c (2026-10-26):
 * the queue's first paint renders no expanded body, except a `?row=` deep
 * link, which loads it at once.
 *
 * Loaded by three paths, all code only (the body's module reads nothing as
 * it evaluates; its reads start when it renders, as before):
 *   - the slot itself, on the expand (useLazyModule: a loaded chunk renders
 *     synchronously for every later expand);
 *   - on idle after the table mounts (`preloadLeadRowExpandedOnIdle`, a
 *     no-op under the idle preloader's rules, e.g. Save-Data);
 *   - when the reader engages with rows (`preloadLeadRowExpanded`: the
 *     keyboard cursor lands on a row), so a first expand rarely waits.
 * Until it lands the row says so in one status line; a chunk that fails says
 * that instead of leaving the expanded row empty (states-07).
 */
import { createIdlePreloader } from '../../lib/prefetch';
import type { LeadRowExpandedProps } from './LeadRowExpanded';
import { lazyModule, useLazyModule } from './useLazyModule';

const EXPANDED_CHUNK = lazyModule(() => import('./LeadRowExpanded'));

/**
 * Intent preload (row engagement): the chunk only. It settles once the load
 * does and never rejects: a failure is the slot's to show, and the slot's own
 * load retries it.
 */
export function preloadLeadRowExpanded(): Promise<void> {
  return EXPANDED_CHUNK.load().then(() => undefined, () => undefined);
}

/** Idle preload from the table's mount effect; returns its cancel. */
export const preloadLeadRowExpandedOnIdle = createIdlePreloader(() => EXPANDED_CHUNK.load());

export function LeadRowExpandedSlot(props: LeadRowExpandedProps) {
  const { module, failed } = useLazyModule(EXPANDED_CHUNK, true);
  const Expanded = module?.LeadRowExpanded;
  if (Expanded) return <Expanded {...props} />;
  // deviation:lead-row-preview-pending: the prototype's BorrowerDetail has no
  // pending or failure state (it renders with its row).
  return (
    <p
      className="tbl__expand-inner muted fs-12 flush"
      role="status"
      data-testid={`lead-preview-pending-${props.lead.borrower_id}`}
    >
      {failed ? 'The preview could not load; reload the page.' : 'Loading the preview…'}
    </p>
  );
}
