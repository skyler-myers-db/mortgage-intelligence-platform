import { describe, expect, it } from 'vitest';
import { sanitizeRumRoute } from './rum';

describe('sanitizeRumRoute', () => {
  it('removes query strings and dynamic borrower ids', () => {
    expect(sanitizeRumRoute('/borrower-360/B-102FL7THC6Q3L?debug=1')).toBe(
      '/borrower-360/:borrower_id',
    );
    expect(sanitizeRumRoute('/offer-orchestrator/B-0OXOBYLW8MNCK')).toBe(
      '/offer-orchestrator/:borrower_id',
    );
    expect(sanitizeRumRoute('/borrower-360/B-Abc_123-extra_456')).toBe(
      '/borrower-360/:borrower_id',
    );
    expect(
      sanitizeRumRoute('/borrower-360/B-a2345678901234567890123456789012345678901234567890'),
    ).toBe('/borrower-360/:borrower_id');
  });

  it('replaces raw UUID path segments', () => {
    expect(
      sanitizeRumRoute('/audit/6469830d-0197-4003-ac9a-372e231c318d'),
    ).toBe('/audit/:uuid');
  });

  it('replaces CLIP and numeric route identifiers', () => {
    expect(sanitizeRumRoute('/property/CL-1234567890')).toBe('/property/:clip_id');
    expect(sanitizeRumRoute('/audit/events/123456789')).toBe('/audit/events/:numeric_id');
  });

  it('keeps stable public routes unchanged', () => {
    expect(sanitizeRumRoute('/segment-intelligence')).toBe('/segment-intelligence');
    expect(sanitizeRumRoute('/lead-queue')).toBe('/lead-queue');
  });
});
