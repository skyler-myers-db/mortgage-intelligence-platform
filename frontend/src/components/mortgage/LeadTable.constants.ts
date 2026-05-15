import type { CallDisposition } from '../../types';
import type { RejectReasonCode } from './LeadTable.types';

/** Concurrency cap for the bulk-approve client-side loop. */
export const BULK_APPROVE_CONCURRENCY = 3;
export const LEAD_ROW_ESTIMATE_PX = 86;
export const LEAD_ROW_OVERSCAN = 12;
export const LEAD_VIRTUALIZATION_THRESHOLD = 120;
export const LEAD_TABLE_COL_COUNT = 15;

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
