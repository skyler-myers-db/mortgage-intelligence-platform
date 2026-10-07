import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router';
import { AsyncState } from '../ui/AsyncState';
import { useMinuteClock } from '../ui/Timestamp';
import type { FollowupItem, FollowupList, SnoozeDays } from '../../lib/apiClients/followUps';
import { pushEscapeLayer } from '../../lib/escapeStack';
import {
  dueQueueHandoff,
  groupFollowups,
  QUEUE_HANDOFF_MAX,
  useFollowUpsList,
  type FollowupSection,
} from '../../lib/followUps';
import { useSnoozeFollowUp } from '../../lib/mutations/followUps';
import { FollowUpsTrayRow } from './FollowUpsTrayRow';
import './FollowUpsTray.css';

/**
 * The Follow-ups tray (D-shell-deviations-a; audit shell-09, wow-power-3;
 * deviation:followups-tray): a non-modal disclosure of the actor's OWN due
 * work, in the top layer (popover="manual", shown after mount) so it sits
 * above the docked Console, the Console bottom sheet and the Genie panel. The
 * prototype has no tray (its `bell` glyph at Module 0 Prototype.html:1008 is
 * never placed); the block reuses `.btn`, `.chip` and the surface tokens.
 *
 * The list is read when the tray opens (audit-free; every row checked
 * against gold). Sections, empty ones omitted: Overdue, Today, This week,
 * Follow-ups you requested, Newly assigned, then two collapsed groups: Older
 * and No longer contactable (every withheld row, and only there). Escape, a
 * pointerdown outside the tray and its trigger, and a route change close it,
 * each returning focus to the trigger. The footer links are followed only on
 * a click: the queue handoff carries at most 20 ids (the Lead Queue's parse
 * cap) and says so when there are more.
 */

export interface FollowUpsTrayProps {
  id: string;
  triggerRef: RefObject<HTMLButtonElement | null>;
  onClose: (refocus: boolean) => void;
}

const OPEN_SECTIONS: ReadonlyArray<{ key: FollowupSection; title: string }> = [
  { key: 'overdue', title: 'Overdue' },
  { key: 'today', title: 'Today' },
  { key: 'week', title: 'This week' },
  { key: 'requested', title: 'Follow-ups you requested' },
  { key: 'assigned', title: 'Newly assigned' },
];
const COLLAPSED_SECTIONS: ReadonlyArray<{ key: FollowupSection; title: string }> = [
  { key: 'older', title: 'Older' },
  { key: 'withheld', title: 'No longer contactable' },
];

export const EMPTY_TRAY = 'Nothing due. Callbacks, follow-ups and new assignments appear here.';

function supportsPopover(element: HTMLElement): boolean {
  return typeof element.showPopover === 'function';
}

export default function FollowUpsTray({ id, triggerRef, onClose }: FollowUpsTrayProps) {
  const trayRef = useRef<HTMLDivElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const headingId = useId();
  const location = useLocation();
  const openedAt = useRef(`${location.pathname}${location.search}`);
  const queryClient = useQueryClient();
  const list = useFollowUpsList(true);
  const now = useMinuteClock();
  const { snooze, pending } = useSnoozeFollowUp(queryClient);
  // Rows snoozed in this open tray leave at once on the 2xx (never before).
  const [snoozed, setSnoozed] = useState<ReadonlySet<string>>(() => new Set());
  const focusAfterSnooze = useRef<string | null>(null);

  useLayoutEffect(() => {
    const tray = trayRef.current;
    if (tray && supportsPopover(tray) && !tray.matches(':popover-open')) tray.showPopover();
  }, []);

  useEffect(() => pushEscapeLayer(() => onClose(true)), [onClose]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (target && (trayRef.current?.contains(target) || triggerRef.current?.contains(target))) return;
      onClose(true);
    };
    window.addEventListener('pointerdown', onPointerDown);
    return () => window.removeEventListener('pointerdown', onPointerDown);
  }, [onClose, triggerRef]);

  const where = `${location.pathname}${location.search}`;
  useEffect(() => {
    if (where !== openedAt.current) onClose(true);
  }, [where, onClose]);

  useEffect(() => {
    const target = focusAfterSnooze.current;
    if (target === null) return;
    focusAfterSnooze.current = null;
    const next = target ? trayRef.current?.querySelector<HTMLElement>(`[data-borrower-id="${target}"] .followups__id`) : null;
    (next ?? headingRef.current)?.focus({ preventScroll: true });
  }, [snoozed]);

  const onSnooze = (item: FollowupItem, days: SnoozeDays) => {
    void snooze(item.borrower_id, days).then((result) => {
      if (result === null) return;
      const rows = [...(trayRef.current?.querySelectorAll<HTMLElement>('.followups__row[data-borrower-id]') ?? [])];
      const index = rows.findIndex((row) => row.dataset.borrowerId === item.borrower_id);
      focusAfterSnooze.current = rows[index + 1]?.dataset.borrowerId ?? '';
      setSnoozed((previous) => new Set(previous).add(item.borrower_id));
    });
  };

  const visible = (data: FollowupList) => data.items.filter((item) => !snoozed.has(item.borrower_id));
  const groups = groupFollowups(list.data ? visible(list.data) : [], now);
  const handoff = dueQueueHandoff(groups);
  const busy = pending !== null;
  const row = (item: FollowupItem) => (
    <FollowUpsTrayRow
      key={item.borrower_id}
      item={item}
      pendingDays={pending?.borrowerId === item.borrower_id ? pending.snoozeDays : null}
      busy={busy}
      onSnooze={onSnooze}
    />
  );

  return (
    <div
      ref={trayRef}
      id={id}
      className="followups"
      role="region"
      aria-labelledby={headingId}
      popover="manual"
    >
      <header className="followups__hdr">
        <h2 ref={headingRef} id={headingId} className="followups__title" tabIndex={-1}>Follow-ups</h2>
      </header>
      <div className="followups__body">
        <AsyncState
          query={list}
          subject="Follow-ups"
          loading={<p className="followups__empty" role="status">Loading follow-ups…</p>}
          isEmpty={(data) => visible(data).length === 0}
          empty={<p className="followups__empty">{EMPTY_TRAY}</p>}
        >
          {() => (
            <>
              {OPEN_SECTIONS.filter(({ key }) => groups[key].length > 0).map(({ key, title }) => (
                <div key={key} className="followups__section">
                  <h3 className="followups__section-title">{title}</h3>
                  <ul className="followups__list">{groups[key].map(row)}</ul>
                </div>
              ))}
              {COLLAPSED_SECTIONS.filter(({ key }) => groups[key].length > 0).map(({ key, title }) => (
                <details key={key} className="followups__section followups__section--collapsed">
                  <summary className="followups__section-title">{`${title} (${groups[key].length})`}</summary>
                  <ul className="followups__list">{groups[key].map(row)}</ul>
                </details>
              ))}
            </>
          )}
        </AsyncState>
      </div>
      <footer className="followups__foot">
        {handoff.ids.length > 0 && (
          <Link to={`/lead-queue?borrower_ids=${handoff.ids.map(encodeURIComponent).join(',')}`}>
            {handoff.more ? `Work the first ${QUEUE_HANDOFF_MAX} due items in the queue` : 'Work due items in the queue'}
          </Link>
        )}
        <Link to="/lead-queue?assigned_to=me">My queue</Link>
      </footer>
    </div>
  );
}
