import { describe, expect, it } from 'vitest';
import {
  ALL_EVENT_TYPES_OPTION,
  AUDIT_EVENT_TYPE_LABELS,
  auditEventCode,
  auditEventLabel,
  eventTypeCodeForOption,
  eventTypeFilterOptions,
  sentenceCaseCode,
} from './AdminAuditExplorer.labels';

describe('audit event labels', () => {
  it('reads a known code as a human label', () => {
    expect(auditEventLabel({ event_type: 'LEAD_EXPORT', action: 'lead_queue.export' })).toBe('Lead list exported');
    expect(auditEventLabel({ event_type: 'APPROVE', action: 'outreach.approve' })).toBe('Outreach approved');
  });

  it('falls back to a sentence-cased code, then to the action', () => {
    expect(auditEventLabel({ event_type: 'APPROVE_OUTREACH', action: 'x' })).toBe('Approve outreach');
    expect(auditEventLabel({ event_type: null, action: 'outreach.approve' })).toBe('Outreach approve');
    expect(auditEventLabel({ event_type: '', action: '' })).toBe('Activity recorded');
    expect(sentenceCaseCode('GENIE_ACTION_CREATE_DRAFT_CAMPAIGN')).toBe('Genie action create draft campaign');
  });

  it('keeps the raw code a row is filed under', () => {
    expect(auditEventCode({ event_type: 'VIEW_LEADS', action: 'leads.view' })).toBe('VIEW_LEADS');
    expect(auditEventCode({ event_type: null, action: 'outreach.approve' })).toBe('outreach.approve');
  });

  it('gives every code a distinct label so the filter has one option per code', () => {
    const labels = Object.values(AUDIT_EVENT_TYPE_LABELS);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it('round-trips every filter option to its code', () => {
    const options = eventTypeFilterOptions(null);
    expect(options[0]).toBe(ALL_EVENT_TYPES_OPTION);
    expect(options).toHaveLength(Object.keys(AUDIT_EVENT_TYPE_LABELS).length + 1);
    for (const [code, label] of Object.entries(AUDIT_EVENT_TYPE_LABELS)) {
      expect(eventTypeCodeForOption(label, null)).toBe(code);
    }
    expect(eventTypeCodeForOption(ALL_EVENT_TYPES_OPTION, 'APPROVE')).toBeNull();
  });

  it('still offers an applied code the registry does not know', () => {
    const options = eventTypeFilterOptions('APPROVE_OUTREACH');
    expect(options[options.length - 1]).toBe('Approve outreach');
    expect(eventTypeCodeForOption('Approve outreach', 'APPROVE_OUTREACH')).toBe('APPROVE_OUTREACH');
  });
});
