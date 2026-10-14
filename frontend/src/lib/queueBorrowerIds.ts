/**
 * How many masked borrower ids a Lead Queue deep link carries
 * (`?borrower_ids=B-…,B-…`; W5d w5-followups-snooze deferral, W5e).
 *
 * One constant for both ends of the hand-off: the queue's URL parse
 * (routes/lead-queue.filters.ts parseBorrowerIds keeps the first N valid,
 * de-duplicated ids) and the Follow-ups tray's "Work due items in the queue"
 * (lib/followUps.ts QUEUE_HANDOFF_MAX), so the tray never offers more ids
 * than the queue reads. Fifty ids keep the link far below the browser and
 * proxy URL limits; the served page is one VIEW_LEADS row whose
 * borrower_ids list holds them.
 */
export const MAX_QUEUE_BORROWER_IDS = 50;
