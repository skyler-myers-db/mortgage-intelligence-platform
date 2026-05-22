export type OutreachChannel = 'email' | 'sms' | 'direct_mail';

export const OUTREACH_CHANNELS: readonly OutreachChannel[] = ['email', 'sms', 'direct_mail'];

export const DEFAULT_REJECT_REASON = 'low_intent';

/** Human-readable threshold labels. Keeps the "if you raised X here" story tangible. */
export const THRESHOLD_LABELS: Record<string, string> = {
  min_spread_bps: 'Min spread (bps)',
  min_equity_pct: 'Min equity (%)',
  heloc_equity_min_pct: 'HELOC equity floor (%)',
  cashout_equity_min_pct: 'Cash-out equity floor (%)',
  retention_min_spread_bps: 'Retention min spread (bps)',
};

export type RejectReasonCode =
  | 'out_of_footprint'
  | 'do_not_call'
  | 'opt_out'
  | 'fair_lending_review'
  | 'low_intent'
  | 'data_quality'
  | 'other_with_text';

export const REJECT_REASONS: { code: RejectReasonCode; label: string }[] = [
  { code: 'low_intent', label: 'Low intent' },
  { code: 'do_not_call', label: 'Do Not Call' },
  { code: 'opt_out', label: 'Opt-out' },
  { code: 'fair_lending_review', label: 'Fair-lending review' },
  { code: 'data_quality', label: 'Data quality' },
  { code: 'out_of_footprint', label: 'Out of footprint' },
  { code: 'other_with_text', label: 'Other' },
];
