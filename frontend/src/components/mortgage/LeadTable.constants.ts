import type { CallDisposition } from '../../types';
import type { RejectReasonCode } from './LeadTable.types';

/** Concurrency cap for the bulk-approve client-side loop. */
export const BULK_APPROVE_CONCURRENCY = 3;
/**
 * One-line rows at the comfortable `--row-h` token (44px; 36px compact; the
 * 1px row rule sits inside it, measured). The old 86px estimate described the
 * stacked-chip rows. Compact rows are shorter; measureElement corrects the
 * estimate on first paint, so the comfortable height is the safe default.
 */
export const LEAD_ROW_ESTIMATE_PX = 44;
/** Borrower 360 preview plus the workflow strip that took the row's timestamps and actions. */
export const LEAD_EXPANDED_PREVIEW_ESTIMATE_PX = 440;
export const LEAD_ROW_OVERSCAN = 12;
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
