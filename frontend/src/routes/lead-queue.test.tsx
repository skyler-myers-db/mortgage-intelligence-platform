import { describe, expect, it } from 'vitest';
import { ApiError } from '../lib/api';
import { buildLeadQueueExportFilters, formatLeadQueueLoadError } from './lead-queue';

describe('buildLeadQueueExportFilters', () => {
  it('exports only normalized allowlisted filters', () => {
    const filters = buildLeadQueueExportFilters({
      stateFilters: ['IL'],
      funnelStage: 'approved',
      targetLenderRef: 'Acme Mortgage',
      targetLenderRefs: ['All', 'Acme Mortgage'],
      portfolioCriteria: {
        product: 'Cash-out',
        target_lender_ref: 'Acme Mortgage',
        owner_name: 'Alice',
        raw_clip: '9154364327',
        street_address: '123 Main Street',
      },
      cohortId: 'f2366c18-e9d7-4354-8400-a29cf212a2fd',
    });

    expect(filters).toContain('states=IL');
    expect(filters).toContain('funnel_stage=approved');
    expect(filters).toContain('target_lender_ref=Acme+Mortgage');
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

  it('does not export unadvertised tenant lender names', () => {
    expect(buildLeadQueueExportFilters({ targetLenderRef: 'Acme Mortgage' })).toBe('none');
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

  it('normalizes the legacy permit-activity deep link to HELOC intent', () => {
    const filters = buildLeadQueueExportFilters({
      portfolioCriteria: {
        purchase_intent: 'Recent permit activity',
      },
    });

    expect(filters).toContain('purchase_intent=HELOC+intent');
    expect(filters).not.toContain('Recent+permit+activity');
  });

  it('drops unsupported permit-like purchase intent labels', () => {
    const filters = buildLeadQueueExportFilters({
      portfolioCriteria: {
        purchase_intent: 'Filed permit activity',
      },
    });

    expect(filters).toBe('none');
  });

  it('exports plural county drilldowns and drops no-op Any portfolio values', () => {
    const filters = buildLeadQueueExportFilters({
      countyFilters: ['12011', '17031'],
      segment: 'itm',
      portfolioCriteria: {
        consent_status: 'Any',
        recency: 'Any',
      },
    });

    expect(filters).toContain('counties=12011%2C17031');
    expect(filters).toContain('segment=itm');
    expect(filters).not.toContain('consent_status');
    expect(filters).not.toContain('recency');
  });
});

describe('formatLeadQueueLoadError', () => {
  it('turns 422 validation responses into filter guidance instead of a raw HTTP code', () => {
    const state = formatLeadQueueLoadError(new ApiError('aged_days: Input should be less than or equal to 90', {
      path: '/api/v1/leads',
      status: 422,
      validationIssues: [
        {
          field: 'aged_days',
          location: ['query', 'aged_days'],
          message: 'Input should be less than or equal to 90',
        },
      ],
    }));

    expect(state.invalidFilters).toBe(true);
    expect(state.message).toContain('Lead queue filters are invalid.');
    expect(state.message).toContain('aged_days: Input should be less than or equal to 90');
    expect(state.message).not.toContain('422');
  });

  it('keeps retry semantics for non-validation errors', () => {
    const state = formatLeadQueueLoadError(new ApiError('Warehouse unavailable', {
      path: '/api/v1/leads',
      status: 503,
    }));

    expect(state.invalidFilters).toBe(false);
    expect(state.message).toBe("Couldn't load leads: Warehouse unavailable");
  });
});
