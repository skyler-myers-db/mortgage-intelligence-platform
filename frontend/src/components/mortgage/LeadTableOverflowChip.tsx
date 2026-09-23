import type { MouseEvent as ReactMouseEvent } from 'react';
import type { DrawerSource } from '../AppContext';
import { useEvidenceHoverCard } from '../EvidenceHoverCard';

/** The accessible-name noun, singular then plural ("1 more status", "2 more statuses"). */
export type LeadOverflowNoun = readonly [one: string, other: string];

/** Hover-card call to action: activating `+n` expands the row, it opens no drawer. */
const OVERFLOW_HOVER_CTA = 'Click to expand the row →';

export interface LeadOverflowItem {
  /** Field name ("Outreach", "Segment"). */
  field: string;
  /** Value text ("Sent", "HELOC Intent"). */
  value: string;
}

/**
 * The `+n` overflow of a one-line lead-table cell (audit tables-04). The cell
 * keeps ONE primary chip; everything else it used to stack is listed by the
 * existing evidence hover-card on hover or keyboard focus, and the button's
 * accessible name spells the hidden values out for screen readers (the card
 * itself is aria-hidden). Activating it expands the row, whose preview holds
 * the full detail and the evidence chips behind it.
 *
 * `.chip` / `.chip--neutral` / `.chip--compact` are the prototype's own `+n`
 * chip (design_files/Module 0 Prototype.html:2044); rendering it as a button
 * is what makes the hidden values reachable by keyboard.
 */
export function LeadTableOverflowChip({
  items,
  noun,
  source,
  onActivate,
}: {
  items: readonly LeadOverflowItem[];
  /** Noun for the accessible name, singular and plural. */
  noun: LeadOverflowNoun;
  /** Where the hidden values come from; the hover card lists them as its signal. */
  source: Pick<DrawerSource, 'title' | 'assetPath' | 'updatedAt'>;
  onActivate: () => void;
}) {
  const summary = items.map((item) => item.value).join(' · ');
  const cardSource: DrawerSource = {
    ...source,
    signals: [{ label: `${items.length} more`, source: source.assetPath ?? source.title, value: summary }],
  };
  const { anchorRef, anchorHandlers, hoverCard } = useEvidenceHoverCard(items.length > 0 ? cardSource : undefined, {
    cta: OVERFLOW_HOVER_CTA,
  });
  if (items.length === 0) return null;
  const spoken = items.map((item) => `${item.field}: ${item.value}`).join(', ');
  return (
    <>
      <button
        type="button"
        className="chip chip--neutral chip--compact lead-table__more"
        ref={anchorRef}
        {...anchorHandlers}
        aria-label={`${items.length} more ${items.length === 1 ? noun[0] : noun[1]}: ${spoken}`}
        onClick={(event: ReactMouseEvent<HTMLButtonElement>) => {
          event.stopPropagation();
          onActivate();
        }}
      >
        <span className="chip__label">+{items.length}</span>
      </button>
      {hoverCard}
    </>
  );
}
