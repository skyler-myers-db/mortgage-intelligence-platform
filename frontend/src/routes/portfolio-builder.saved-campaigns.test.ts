import { describe, expect, it } from 'vitest';
import type { CampaignSummary } from '../types';
import { savedCampaignVariants } from './portfolio-builder.saved-campaigns';

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
});
