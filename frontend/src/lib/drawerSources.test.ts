import { describe, expect, it } from 'vitest';
import { descriptorFor, DRAWER_SOURCES } from './drawerSources';

describe('descriptorFor', () => {
  it('routes lead score lineage to the lead score drawer', () => {
    expect(descriptorFor('mip.gold.fn_lead_score')).toBe(DRAWER_SOURCES.leadScore);
    expect(descriptorFor('mip.gold.lead_scores')).toBe(DRAWER_SOURCES.leadScore);
  });

  it('keeps next-best-offer lineage separate from lead score lineage', () => {
    expect(descriptorFor('mip.gold.fn_next_best_offer')).toBe(DRAWER_SOURCES.nbo);
  });
});
