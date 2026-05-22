import { THRESHOLD_LABELS } from './offer-orchestrator.constants';

/** Short, presenter-friendly label that fits in a chip. */
export function shortSourceLabel(source: string): string {
  return source.split('.').pop() ?? source;
}

export function humanizeThresholdKey(k: string): string {
  if (THRESHOLD_LABELS[k]) return THRESHOLD_LABELS[k];
  // Reasonable fallback: snake_case -> Title Case
  return k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
