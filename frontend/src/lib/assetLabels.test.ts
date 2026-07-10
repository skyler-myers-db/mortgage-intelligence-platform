import { describe, expect, it } from 'vitest';
import { friendlyAssetLabel, humanizeAssetMentions } from './assetLabels';

describe('friendlyAssetLabel', () => {
  it('maps known gold/semantic assets to plain labels (bare, schema, and FQ forms)', () => {
    expect(friendlyAssetLabel('mip.gold.borrower_360')).toBe('Borrower 360');
    expect(friendlyAssetLabel('gold.lead_population')).toBe('Lead population');
    expect(friendlyAssetLabel('evidence_events')).toBe('Evidence events');
    expect(friendlyAssetLabel('acme_mip.semantics.segment_performance_metric_view')).toBe('Segment performance view');
  });

  it('maps silver source tables used in the Signals evidence rows', () => {
    expect(friendlyAssetLabel('market_rates_weekly')).toBe('Weekly market rates');
    expect(friendlyAssetLabel('lien_current')).toBe('Current liens');
    expect(friendlyAssetLabel('mip.silver.property_master')).toBe('Property master');
  });

  it('resolves a trailing .column to the table label', () => {
    expect(friendlyAssetLabel('mip.gold.evidence_events.timestamp')).toBe('Evidence events');
  });

  it('humanizes unknown assets instead of leaking a raw identifier', () => {
    expect(friendlyAssetLabel('mip.gold.some_new_table')).toBe('Some new table');
  });
});

describe('humanizeAssetMentions', () => {
  it('replaces raw table mentions inside free-text captions', () => {
    expect(humanizeAssetMentions('Broad count uses borrower_360.in_the_money.')).toBe('Broad count uses Borrower 360.');
    expect(humanizeAssetMentions('Rows from mip.gold.evidence_events feed the chart.')).toBe('Rows from Evidence events feed the chart.');
  });

  it('leaves text without known tables untouched', () => {
    expect(humanizeAssetMentions('Actionable count requires Lead Queue eligibility and opt-in.'))
      .toBe('Actionable count requires Lead Queue eligibility and opt-in.');
  });
});
