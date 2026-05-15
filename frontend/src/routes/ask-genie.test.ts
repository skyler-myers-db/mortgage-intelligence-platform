import { describe, expect, it } from 'vitest';
import { buildTrustedAssetQuestion } from './ask-genie';

describe('buildTrustedAssetQuestion', () => {
  it('uses the exact trusted UC path and business label without adding backend-only wording', () => {
    const prompt = buildTrustedAssetQuestion({
      label: 'Lead-generation metric view',
      path: 'mip.semantics.lead_generation_metric_view',
    });

    expect(prompt).toContain('mip.semantics.lead_generation_metric_view');
    expect(prompt).toContain('Module 0');
    expect(prompt).toContain('lead-generation metric view');
    expect(prompt).not.toContain('/api/');
    expect(prompt).not.toContain('audit event');
  });
});
