import { describe, expect, it } from 'vitest';
import {
  buildTrustedAssetQuestion,
  formatGrowthAgentCount,
  parseGrowthAgentStateInput,
  trustedAssetsForCatalog,
} from './ask-genie';

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

describe('trustedAssetsForCatalog', () => {
  it('uses backend-provided catalog-qualified trusted assets when available', () => {
    const assets = trustedAssetsForCatalog([
      'acme_mip.gold.lead_population',
      'acme_mip.semantics.lead_generation_metric_view',
    ]);

    expect(assets.find((asset) => asset.label === 'Ranked lead population')?.path)
      .toBe('acme_mip.gold.lead_population');
    expect(assets.find((asset) => asset.label === 'Lead-generation metric view')?.path)
      .toBe('acme_mip.semantics.lead_generation_metric_view');
    expect(assets.find((asset) => asset.label === 'Borrower 360 profile')?.path)
      .toBe('gold.borrower_360');
  });

  it('uses catalog-relative labels before the backend start payload arrives', () => {
    const assets = trustedAssetsForCatalog(undefined);

    expect(assets.map((asset) => asset.path)).not.toContain('mip.gold.borrower_360');
    expect(assets.find((asset) => asset.label === 'Borrower 360 profile')?.path)
      .toBe('gold.borrower_360');
  });
});

describe('parseGrowthAgentStateInput', () => {
  it('normalizes, de-duplicates, and preserves order for state scopes', () => {
    expect(parseGrowthAgentStateInput(' il, TX ca IL ')).toEqual({
      states: ['IL', 'TX', 'CA'],
      invalid: [],
    });
  });

  it('fails closed on non-USPS-shaped tokens', () => {
    expect(parseGrowthAgentStateInput('IL illinois 123')).toEqual({
      states: ['IL'],
      invalid: ['illinois', '123'],
    });
  });
});

describe('formatGrowthAgentCount', () => {
  it('formats null-safe positive counts for run cards', () => {
    expect(formatGrowthAgentCount(5394)).toBe('5,394');
    expect(formatGrowthAgentCount(null)).toBe('0');
  });
});
