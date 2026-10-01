import { useEffect, useRef } from 'react';
import { Link } from 'react-router';
import { formatCount } from '../../lib/formatters';

/**
 * The export confirmation strip (audit tables-08; D-approval-flow-b). A
 * loaded-rows export of a cohort larger than the rows on screen says it is
 * partial and points at the honest way to work every match: build a campaign
 * (Portfolio Builder with the queue's filters, lead-queue.handoff). With no
 * handoff (Segment Intelligence) the sentence carries no link.
 *
 * While the strip holds the pointer or keyboard focus it does not retire
 * (WCAG 2.2.1): `onHold` reports that, through DOM listeners rather than JSX
 * handlers, so the status region gains no interaction handler.
 */
export function LeadExportNotice({
  notice,
  truncatedOf,
  campaignHref,
  onHold,
}: {
  notice: string;
  /** How many borrowers matched when only the loaded ones were exported; else null. */
  truncatedOf: number | null;
  campaignHref: string | null;
  onHold: (held: boolean) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    let pointer = false;
    let focus = false;
    const report = () => onHold(pointer || focus);
    const enter = () => {
      pointer = true;
      report();
    };
    const leave = () => {
      pointer = false;
      report();
    };
    const focusIn = () => {
      focus = true;
      report();
    };
    const focusOut = (event: FocusEvent) => {
      if (event.relatedTarget instanceof Node && node.contains(event.relatedTarget)) return;
      focus = false;
      report();
    };
    node.addEventListener('pointerenter', enter);
    node.addEventListener('pointerleave', leave);
    node.addEventListener('focusin', focusIn);
    node.addEventListener('focusout', focusOut);
    return () => {
      node.removeEventListener('pointerenter', enter);
      node.removeEventListener('pointerleave', leave);
      node.removeEventListener('focusin', focusIn);
      node.removeEventListener('focusout', focusOut);
      onHold(false);
    };
  }, [onHold]);

  return (
    <div ref={ref} role="status" aria-live="polite" className="table-success" data-testid="lead-export-notice">
      {notice}
      {truncatedOf !== null && (
        <>
          {` Only the loaded leads were exported. To work all ${formatCount(truncatedOf)}, `}
          {campaignHref
            ? <Link to={campaignHref} data-testid="lead-export-campaign-link">build a campaign</Link>
            : 'build a campaign'}
          .
        </>
      )}
    </div>
  );
}
