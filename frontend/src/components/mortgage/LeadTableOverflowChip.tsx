import type { MouseEvent as ReactMouseEvent } from 'react';
import type { DrawerSource } from '../AppContext';
import { useEvidenceHoverCard } from '../EvidenceHoverCard';

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
  /** Plural noun for the accessible name ("segments", "statuses"). */
  noun: string;
  /** Where the hidden values come from; the hover card lists them as its signal. */
  source: Pick<DrawerSource, 'title' | 'assetPath' | 'updatedAt'>;
  onActivate: () => void;
}) {
  const summary = items.map((item) => item.value).join(' · ');
  const cardSource: DrawerSource = {
    ...source,
    signals: [{ label: `${items.length} more`, source: source.assetPath ?? source.title, value: summary }],
  };
  const { anchorRef, anchorHandlers, hoverCard } = useEvidenceHoverCard(items.length > 0 ? cardSource : undefined);
  if (items.length === 0) return null;
  const spoken = items.map((item) => `${item.field}: ${item.value}`).join(', ');
  return (
    <>
      <button
        type="button"
        className="chip chip--neutral chip--compact lead-table__more"
        ref={anchorRef}
        {...anchorHandlers}
        aria-label={`${items.length} more ${noun}: ${spoken}`}
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
