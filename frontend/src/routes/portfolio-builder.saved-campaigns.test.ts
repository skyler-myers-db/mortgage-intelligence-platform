import { describe, expect, it } from 'vitest';
import type { CampaignSummary } from '../types';
import {
  savedCampaignCanArchive,
  savedCampaignVariants,
} from './portfolio-builder.saved-campaigns';

function campaignWithVariant(variant: Record<string, unknown>): CampaignSummary {
  return {
    campaign_id: '11111111-1111-4111-8111-111111111111',
    name: 'Saved campaign',
    owner_email: 'growth@summit.example',
    status: 'draft',
    criteria: {},
    message_variants: [{
      variant_name: 'A',
      generation_mode: 'supervisor',
      generator_label: 'Databricks Agent Responses',
      ...variant,
    }],
  };
}

describe('savedCampaignVariants', () => {
  it('uses the server-owned durable verification fact after tokens are stripped', () => {
    const [variant] = savedCampaignVariants(campaignWithVariant({
      provenance_key_id: 'v1',
      provenance_copy_hash: 'a'.repeat(64),
      copy_verified_at_creation: true,
    }));

    expect(variant.verifiedAtCreation).toBe(true);
  });

  it('does not infer trust from a client-provided token', () => {
    const [variant] = savedCampaignVariants(campaignWithVariant({
      provenance_token: 'signed-but-not-a-server-list-contract',
      copy_verified_at_creation: false,
    }));

    expect(variant.verifiedAtCreation).toBe(false);
  });

  it('normalizes legacy server labels without broad substring rewriting', () => {
    const [legacy] = savedCampaignVariants(campaignWithVariant({
      generator_label: 'Agent endpoint-generated recommendation',
    }));
    const [unknown] = savedCampaignVariants(campaignWithVariant({
      generator_label: 'Custom tenant agent',
    }));

    expect(legacy.generatorLabel).toBe('Databricks Agent Responses');
    expect(unknown.generatorLabel).toBe('Custom tenant agent');
  });

  it('does not expose variants from a quarantined campaign', () => {
    const campaign = campaignWithVariant({ copy_verified_at_creation: true });
    campaign.actionable = false;
    campaign.actionability_issue = 'legacy_contract';

    expect(savedCampaignVariants(campaign)).toEqual([]);
  });
});

describe('savedCampaignCanArchive', () => {
  it('allows only legacy or failed immutable-treatment quarantines', () => {
    const legacy = campaignWithVariant({});
    legacy.actionable = false;
    legacy.actionability_issue = 'treatment_unbound';
    legacy.treatment_state = 'legacy_unbound';
    expect(savedCampaignCanArchive(legacy)).toBe(true);

    expect(savedCampaignCanArchive({ ...legacy, treatment_state: 'failed' })).toBe(true);
    expect(savedCampaignCanArchive({ ...legacy, treatment_state: 'building' })).toBe(false);
    expect(savedCampaignCanArchive({ ...legacy, treatment_state: 'ready' })).toBe(false);
    expect(savedCampaignCanArchive({ ...legacy, actionability_issue: 'invalid_criteria' })).toBe(false);
    expect(savedCampaignCanArchive({ ...legacy, actionable: true })).toBe(false);
  });

  it('keeps the archive remediation available during an issue-only rolling deploy', () => {
    const campaign = campaignWithVariant({});
    campaign.actionable = false;
    campaign.actionability_issue = 'treatment_unbound';

    expect(savedCampaignCanArchive(campaign)).toBe(true);
  });
});
