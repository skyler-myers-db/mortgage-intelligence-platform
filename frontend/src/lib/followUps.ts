/**
 * The Follow-ups tray's list read and its local-day sections
 * (D-shell-deviations-a; audit shell-09, wow-power-3). Lazy: the tray chunk
 * imports it, the rail never does.
 *
 * The list runs only while the tray is open (stale after 60 s), through the
 * shared warming-up retry, so a 503 shows WarmingUpBlock or the described
 * failure with Retry. Rows are bucketed by the VIEWER's local day:
 *
 *   overdue    before the local start of today, at most 14 days ago
 *   today      before the next local midnight
 *   week       later inside the server's window (7 days)
 *   requested  follow-ups the viewer decided that someone else owns
 *   assigned   new assignments (no due time)
 *   older      due more than 14 days ago (a collapsed group)
 *   withheld   EVERY contactable=false row of any kind, and only there
 */
import { fetchFollowupList, type FollowupItem, type FollowupList } from './apiClients/followUps';
import { endOfLocalDay, instantOf, startOfLocalDay } from './followUpsDay';
import { MAX_QUEUE_BORROWER_IDS } from './queueBorrowerIds';
import { queryKeys } from './queryKeys';
import { useWarmingUpRetry } from './useWarmingUpRetry';

export const FOLLOW_UPS_LIST_STALE_MS = 60_000;
export const FOLLOW_UPS_WINDOW_DAYS = 7;
/** The overdue horizon: older rows collapse under "Older", and never light the dot. */
export const OVERDUE_HORIZON_MS = 14 * 24 * 60 * 60 * 1000;
/** The Lead Queue's borrower_ids parse cap (routes/lead-queue.filters.ts parseBorrowerIds). */
export const QUEUE_HANDOFF_MAX = MAX_QUEUE_BORROWER_IDS;

export type FollowupSection = 'overdue' | 'today' | 'week' | 'requested' | 'assigned' | 'older' | 'withheld';

export const SECTION_ORDER: readonly FollowupSection[] = [
  'overdue', 'today', 'week', 'requested', 'assigned', 'older', 'withheld',
];

export function useFollowUpsList(open: boolean, windowDays: number = FOLLOW_UPS_WINDOW_DAYS) {
  return useWarmingUpRetry<FollowupList>((signal) => fetchFollowupList(windowDays, signal), {
    queryKey: queryKeys.followUpsList(windowDays),
    enabled: open,
    staleTime: FOLLOW_UPS_LIST_STALE_MS,
  });
}

export function sectionFor(item: FollowupItem, now: number): FollowupSection {
  if (!item.contactable) return 'withheld';
  if (item.kind === 'requested_follow_up') return 'requested';
  if (item.kind === 'new_assignment') return 'assigned';
  const due = instantOf(item.due_at);
  if (due === null) return 'week';
  if (due < now - OVERDUE_HORIZON_MS) return 'older';
  if (due < startOfLocalDay(now)) return 'overdue';
  if (due < endOfLocalDay(now)) return 'today';
  return 'week';
}

export function groupFollowups(
  items: readonly FollowupItem[],
  now: number,
): Record<FollowupSection, FollowupItem[]> {
  const groups: Record<FollowupSection, FollowupItem[]> = {
    overdue: [], today: [], week: [], requested: [], assigned: [], older: [], withheld: [],
  };
  for (const item of items) groups[sectionFor(item, now)].push(item);
  return groups;
}

/** The queue handoff: Overdue + Today contactable ids, at most the queue's parse cap. */
export function dueQueueHandoff(groups: Record<FollowupSection, FollowupItem[]>): { ids: string[]; more: boolean } {
  const ids = [...groups.overdue, ...groups.today].map((item) => item.borrower_id);
  return { ids: ids.slice(0, QUEUE_HANDOFF_MAX), more: ids.length > QUEUE_HANDOFF_MAX };
}
