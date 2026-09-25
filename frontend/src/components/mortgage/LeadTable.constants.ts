import type { CallDisposition } from '../../types';
import type { Density } from '../../lib/themePreference';
import type { RejectReasonCode } from './LeadTable.types';

/** Concurrency cap for the bulk-approve client-side loop. */
export const BULK_APPROVE_CONCURRENCY = 3;
/**
 * The backend's mutation budget (settings.mip_rate_limit_mutation_per_minute;
 * draft, approve and reject all draw on it). A bulk run's ETA never promises
 * a faster pace than this: tests/unit/test_bulk_eta_budget_parity.py pins it.
 */
export const MUTATION_BUDGET_PER_MINUTE = 120;
/**
 * One-line rows at the `--row-h` token: 44px comfortable, 36px compact (the
 * 1px row rule sits inside it, measured). The old 86px estimate described
 * the stacked-chip rows. measureElement corrects an estimate on first paint;
 * one that follows the density (audit responsive-09 item 3) keeps the first
 * virtual window and the scrollbar right before it does. An unknown density
 * reads as comfortable, the taller and so the safer estimate.
 */
export function leadRowEstimatePx(density: Density | null | undefined): number {
  return density === 'compact' ? 36 : 44;
}
/**
 * Borrower 360 preview plus the workflow strip that took the row's timestamps
 * and actions: measured at 514px at 1440x900, so the estimate starts there.
 */
export const LEAD_EXPANDED_PREVIEW_ESTIMATE_PX = 520;
/**
 * Rows rendered past each edge of the scrollport (audit runtime-04 slice 1):
 * 5 keeps a J / K step and a wheel notch inside rendered rows at 44px, and
 * renders 14 fewer rows per window than the old 12.
 */
export const LEAD_ROW_OVERSCAN = 5;
export const LEAD_VIRTUALIZATION_THRESHOLD = 120;

export const REJECT_REASONS: { code: RejectReasonCode; label: string }[] = [
  { code: 'low_intent', label: 'Low intent' },
  { code: 'do_not_call', label: 'Do Not Call' },
  { code: 'opt_out', label: 'Opt-out' },
  { code: 'fair_lending_review', label: 'Fair-lending review' },
  { code: 'data_quality', label: 'Data quality' },
  { code: 'out_of_footprint', label: 'Out of footprint' },
  { code: 'other_with_text', label: 'Other' },
];

export const DISPOSITION_OPTIONS: { outcome: CallDisposition['outcome']; label: string }[] = [
  { outcome: 'called_no_answer', label: 'No answer' },
  { outcome: 'called_left_voicemail', label: 'Left voicemail' },
  { outcome: 'connected', label: 'Connected' },
  { outcome: 'callback_scheduled', label: 'Callback scheduled' },
  { outcome: 'application_started', label: 'Application started' },
  { outcome: 'not_interested', label: 'Not interested' },
  { outcome: 'not_now', label: 'Not now' },
  { outcome: 'dead', label: 'Dead lead' },
];
