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

  it('templates any Ask Genie conversation segment, malformed or not (audit shell-03)', () => {
    for (const id of [
      '0123456789abcdef0123456789abcdef',
      '01234567-89ab-cdef-0123-456789abcdef',
      'not-an-id',
      'Alice%20Smith',
      'a/b',
    ]) {
      expect(sanitizeRumRoute(`/ask-genie/${id}`)).toBe('/ask-genie/:conversation_id');
      expect(sanitizeRumRoute(`/ask-genie/${id}?tab=ask#latest`)).toBe('/ask-genie/:conversation_id');
    }
    // The index route and a trailing slash hold no conversation segment.
    expect(sanitizeRumRoute('/ask-genie')).toBe('/ask-genie');
    expect(sanitizeRumRoute('/ask-genie?tab=history')).toBe('/ask-genie');
    expect(sanitizeRumRoute('/ask-genie/')).toBe('/ask-genie/');
  });

  it('templates a bare 32-hex segment on any other path', () => {
    expect(sanitizeRumRoute('/audit/0123456789ABCDEF0123456789abcdef')).toBe('/audit/:hex_id');
    // Not a whole segment: left alone.
    expect(sanitizeRumRoute('/glossary/x0123456789abcdef0123456789abcdef')).toBe(
      '/glossary/x0123456789abcdef0123456789abcdef',
    );
  });

  it('keeps stable public routes unchanged', () => {
    expect(sanitizeRumRoute('/segment-intelligence')).toBe('/segment-intelligence');
    expect(sanitizeRumRoute('/lead-queue')).toBe('/lead-queue');
  });
});
