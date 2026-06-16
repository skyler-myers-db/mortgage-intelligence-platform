import { describe, expect, it } from 'vitest';
import {
  approvalFilterDisplayValue,
  funnelStageDisplayValue,
  outreachFilterDisplayValue,
  segmentDisplayLabel,
  segmentFilterDisplayValue,
} from './lead-queue';

describe('lead queue effective workflow filters', () => {
  it('shows Approved when the approved funnel stage is driving the filter', () => {
    expect(approvalFilterDisplayValue('any', 'approved')).toBe('Approved');
  });

  it('shows explicit approval status before funnel-derived status', () => {
    expect(approvalFilterDisplayValue('rejected', 'approved')).toBe('Rejected');
  });

  it('shows Actioned when the actioned funnel stage is driving the filter', () => {
    expect(outreachFilterDisplayValue('any', 'actioned')).toBe('Actioned');
  });

  it('falls back to Any labels when no workflow filter is active', () => {
    expect(approvalFilterDisplayValue('any')).toBe('Any approval');
    expect(outreachFilterDisplayValue('any')).toBe('Any outreach');
  });
});

describe('lead queue drilldown display labels', () => {
  it('shows every analytics funnel stage as a user-facing filter value', () => {
    expect(funnelStageDisplayValue('addressable')).toBe('Addressable');
    expect(funnelStageDisplayValue('in_the_money')).toBe('Refi economics');
    expect(funnelStageDisplayValue('high_opportunity')).toBe('Opportunity score 75+');
    expect(funnelStageDisplayValue('offer_recommended')).toBe('Primary offer selected');
    expect(funnelStageDisplayValue('approved')).toBe('Approved');
    expect(funnelStageDisplayValue('actioned')).toBe('Actioned');
  });

  it('maps segment drilldown codes to visible filter names', () => {
    expect(segmentDisplayLabel('listed')).toBe('Listed for Sale');
    expect(segmentDisplayLabel('permit')).toBe('HELOC Intent');
    expect(segmentFilterDisplayValue('listed')).toBe('Listed for Sale');
    expect(segmentFilterDisplayValue('permit')).toBe('HELOC Intent');
    expect(segmentFilterDisplayValue(undefined, ['itm', 'equity'])).toBe('2 segments selected');
    expect(segmentFilterDisplayValue()).toBe('All segments');
  });
});
