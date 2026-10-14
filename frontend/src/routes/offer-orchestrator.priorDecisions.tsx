/**
 * The Offer's "Prior decisions (n)" disclosure (audit flow-04 phase 2,
 * D-audit-reads-c2): the borrower decision history, collapsed by default, so
 * an approver sees a colleague's rejection or a contact block before deciding.
 *
 * A thin lazy wrapper: the history ships in its own chunk (shared with
 * Borrower 360), loaded on this mount. Its read is audit-free and is not part
 * of queryKeys.offerSnapshot, so the Offer's single-observer contract for the
 * audited snapshot reads is unaffected. It runs on mount for the count; a
 * chunk that cannot load renders nothing.
 */
import { lazyModule, useLazyModule } from '../components/mortgage/useLazyModule';

const DECISION_HISTORY_CHUNK = lazyModule(() => import('../components/mortgage/BorrowerDecisionHistory'));

export function OfferPriorDecisions({ borrowerId }: { borrowerId: string }) {
  const { module } = useLazyModule(DECISION_HISTORY_CHUNK, true);
  const History = module?.BorrowerDecisionHistory;
  return History ? <History borrowerId={borrowerId} variant="disclosure" /> : null;
}
