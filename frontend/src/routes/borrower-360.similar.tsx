import { useId, useState } from 'react';
import type { LeadSummary } from '../types';
import { useApp } from '../components/AppContext';
import { Button } from '../components/Primitives';
import { lazyModule, useLazyModule } from '../components/mortgage/useLazyModule';

/**
 * Borrower 360's "More like this" entry (wow-power-2, W5e item 4d;
 * deviation:more-like-this), beside "Build outreach draft" on the Primary
 * offer card. The panel (components/mortgage/SimilarCohortPanel) is never
 * imported statically: its chunk loads on the click, so the route closure
 * gains only this trigger, and its one read (the audit-free count) happens
 * on that open. Links only; the Triage deck link is an approver's.
 */
const SIMILAR_CHUNK = lazyModule(() => import('../components/mortgage/SimilarCohortPanel'));

export function BorrowerMoreLikeThis({ borrower }: { borrower: LeadSummary }) {
  const { canApprove } = useApp();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const chunk = useLazyModule(SIMILAR_CHUNK, open);
  const Panel = chunk.module?.SimilarCohortPanel;
  return (
    <>
      <Button
        variant="ghost"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}
        data-testid="borrower-more-like-this"
      >
        More like this
      </Button>
      {open && (Panel ? (
        <Panel id={panelId} lead={borrower} canApprove={canApprove === true} />
      ) : (
        <p id={panelId} className="muted fs-12 flush" role="status">
          {chunk.failed ? 'More like this could not load; reload the page.' : 'Loading…'}
        </p>
      ))}
    </>
  );
}
