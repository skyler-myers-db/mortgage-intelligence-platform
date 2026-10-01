/**
 * A bulk run's copy that only the lazy bulk chunk shows (audit tables-07;
 * D-approval-flow-a1 / -d): the ETA and the canary line. Kept out of
 * useLeadBulkRun, which rides the LeadTable chunk (its result sentence stays
 * there: the static run fallback reads it).
 */
import { formatCount } from '../../lib/formatters';
import type { BulkRunCanary } from './useLeadBulkRun';

export function formatMinutesLeft(minutes: number): string {
  return `about ${formatCount(Math.max(1, Math.ceil(minutes)))} min left`;
}

/** The gate's line after a canary stop: which row was refused, and why. */
export function bulkCanaryNotice(canary: BulkRunCanary): string {
  return `Nothing else was sent: ${canary.borrowerId} was refused: ${canary.message ?? 'the request did not go through'}`;
}
