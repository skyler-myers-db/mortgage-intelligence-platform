/**
 * The explorer's date presets (W5d, tables-10;
 * deviation:audit-explorer-controls): Today, Last 7 days and Last 30 days
 * apply at once through the existing audit_since / audit_until parameters,
 * as inclusive LOCAL calendar days ending today. The other applied filters are kept; the single-row deep
 * link (eventId) is cleared, since the reader is asking a new question.
 */
import type { AuditExplorerFilters } from './AdminAuditExplorer.params';

export interface AuditDatePreset {
  label: string;
  /** Days before today the window starts (0 = today only). */
  daysBack: number;
}

export const AUDIT_DATE_PRESETS: readonly AuditDatePreset[] = [
  { label: 'Today', daysBack: 0 },
  { label: 'Last 7 days', daysBack: 6 },
  { label: 'Last 30 days', daysBack: 29 },
];

/** `YYYY-MM-DD` of a local calendar day. */
function localDay(date: Date): string {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}

export function presetWindow(preset: AuditDatePreset, now: Date = new Date()): { since: string; until: string } {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - preset.daysBack);
  return { since: localDay(start), until: localDay(now) };
}

/** Whether the applied window is exactly this preset's window today. */
export function isPresetApplied(applied: AuditExplorerFilters, preset: AuditDatePreset, now: Date = new Date()): boolean {
  const range = presetWindow(preset, now);
  return applied.since === range.since && applied.until === range.until;
}

/** The applied filters with the preset's window, the rest kept, the deep link dropped. */
export function withPreset(applied: AuditExplorerFilters, preset: AuditDatePreset, now: Date = new Date()): AuditExplorerFilters {
  return { ...applied, ...presetWindow(preset, now), eventId: '' };
}
