import { describe, expect, it } from 'vitest';
import { buildLeadQueueExportFilters } from './lead-queue';

describe('buildLeadQueueExportFilters', () => {
  it('exports only normalized allowlisted filters', () => {
    const filters = buildLeadQueueExportFilters({
      stateFilters: ['IL'],
      targetLenderRef: 'Competitor A',
      portfolioCriteria: {
        product: 'Cash-out',
        owner_name: 'Alice',
        raw_clip: '9154364327',
        street_address: '123 Main Street',
      },
      cohortId: 'f2366c18-e9d7-4354-8400-a29cf212a2fd',
    });

    expect(filters).toContain('states=IL');
    expect(filters).toContain('target_lender_ref=Competitor+A');
    expect(filters).toContain('product=Cash-out');
    expect(filters).toContain('cohort_id=f2366c18-e9d7-4354-8400-a29cf212a2fd');
    expect(filters).not.toContain('owner_name');
    expect(filters).not.toContain('raw_clip');
    expect(filters).not.toContain('street_address');
    expect(filters).not.toContain('Alice');
    expect(filters).not.toContain('9154364327');
  });

  it('drops unreviewed cohort ids and renders none when no safe filters exist', () => {
    expect(buildLeadQueueExportFilters({ cohortId: 'raw_clip=9154364327' })).toBe('none');
  });

  it('drops unreviewed portfolio filter values even for allowed keys', () => {
    const filters = buildLeadQueueExportFilters({
      cohortId: 'f2366c18-e9d7-4354-8400-a29cf212a2fd',
      portfolioCriteria: {
        product: 'Alice Smith',
        occupancy: '123 Main Street',
        lender_relationship: 'Competitor customer',
      },
    });

    expect(filters).toContain('cohort_id=f2366c18-e9d7-4354-8400-a29cf212a2fd');
    expect(filters).toContain('lender_relationship=Competitor+customer');
    expect(filters).not.toContain('Alice');
    expect(filters).not.toContain('Smith');
    expect(filters).not.toContain('123+Main');
  });
});
