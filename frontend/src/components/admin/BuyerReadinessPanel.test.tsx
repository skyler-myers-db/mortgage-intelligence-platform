import { describe, expect, it } from 'vitest';
import { buyerReadinessItems } from './BuyerReadinessPanel';
import type { ActivationSummary } from '../../types';

function itemByLabel(items: ReturnType<typeof buyerReadinessItems>, label: string) {
  const item = items.find((entry) => entry.label === label);
  if (!item) throw new Error(`Missing readiness item ${label}`);
  return item;
}

describe('buyerReadinessItems', () => {
  it('keeps CRM delivery claims gated when Salesforce is not configured', () => {
    const activation: ActivationSummary = {
      destinations: [
        {
          destination_key: 'salesforce',
          destination_type: 'salesforce',
          display_name: 'Salesforce',
          status: 'not_configured',
          allowed_actions: [],
        },
      ],
      recent_outbox: [],
    };

    const items = buyerReadinessItems(activation, []);
    const crm = itemByLabel(items, 'CRM / Salesforce handoff');
    const activationItem = itemByLabel(items, 'Activation / outreach');

    expect(crm.value).toBe('Not configured');
    expect(crm.status).toBe('setup required');
    expect(crm.detail).toContain('requires a customer-specific connector');
    expect(activationItem.status).toBe('no auto-send');
    expect(activationItem.detail).toContain('does not auto-send email or SMS');
  });

  it('allows connected Salesforce language only when the destination is connected', () => {
    const activation: ActivationSummary = {
      destinations: [
        {
          destination_key: 'salesforce',
          destination_type: 'salesforce',
          display_name: 'Salesforce',
          status: 'connected',
          allowed_actions: ['stage_activation'],
        },
      ],
      recent_outbox: [
        {
          activation_id: 'act_1',
          destination_key: 'salesforce',
          destination_type: 'salesforce',
          destination_display_name: 'Salesforce',
          destination_status: 'connected',
          entity_type: 'borrower',
          entity_id: 'B-1',
          borrower_id: 'B-1',
          approval_id: 'apr_1',
          channel: 'email',
          status: 'delivered',
          request_id: '00000000-0000-4000-8000-000000000001',
          created_by: 'qa@example.com',
          created_at: '2026-06-25T00:00:00Z',
          updated_at: '2026-06-25T00:00:00Z',
        },
      ],
    };

    const items = buyerReadinessItems(activation, []);
    const crm = itemByLabel(items, 'CRM / Salesforce handoff');
    const activationItem = itemByLabel(items, 'Activation / outreach');

    expect(crm.value).toBe('Connected destination');
    expect(crm.status).toBe('connected');
    expect(crm.detail).toContain('configured Salesforce');
    expect(activationItem.value).toBe('1 delivered row');
    expect(activationItem.status).toBe('delivery observed');
  });

  it('separates live, synthetic, and pending source-readiness claims', () => {
    const items = buyerReadinessItems(
      null,
      [
        { name: 'cotality_public_records', status: 'live', rows: 100, last_updated: null, note: '' },
        { name: 'summit_servicing', status: 'demo_synthetic', rows: 20, last_updated: null, note: '' },
        { name: 'building_permits', status: 'not_configured', rows: null, last_updated: null, note: '' },
      ],
    );
    const source = itemByLabel(items, 'Data readiness');

    expect(source.value).toBe('1 live · 1 synthetic · 1 pending');
    expect(source.status).toBe('partial');
    expect(source.detail).toContain('cannot imply everything is live');
  });

  it('keeps data-source claims in loading state while source readiness is still probing', () => {
    const items = buyerReadinessItems(null, undefined, true);
    const source = itemByLabel(items, 'Data readiness');

    expect(source.value).toBe('Loading');
    expect(source.status).toBe('probing');
    expect(source.detail).toContain('Checking source-readiness rows');
  });

  it('does not invent activation state when the registry is unavailable', () => {
    const items = buyerReadinessItems(undefined, [], false, false, true);
    const crm = itemByLabel(items, 'CRM / Salesforce handoff');
    const activationItem = itemByLabel(items, 'Activation / outreach');

    expect(crm.value).toBe('Unknown');
    expect(crm.status).toBe('registry unavailable');
    expect(crm.detail).toContain('Do not claim live CRM');
    expect(activationItem.value).toBe('Unknown');
    expect(activationItem.status).toBe('unverified');
  });

  it('does not invent activation state while the registry is still loading', () => {
    const items = buyerReadinessItems(undefined, [], false, false, false, true);
    const crm = itemByLabel(items, 'CRM / Salesforce handoff');
    const activationItem = itemByLabel(items, 'Activation / outreach');

    expect(crm.value).toBe('Checking');
    expect(crm.status).toBe('probing registry');
    expect(crm.detail).toContain('unverified until this resolves');
    expect(activationItem.value).toBe('Checking');
    expect(activationItem.status).toBe('probing outbox');
  });

  it('pins deterministic scoring, certification, and audit claim boundaries', () => {
    const items = buyerReadinessItems(null, []);

    expect(itemByLabel(items, 'Scoring / recommendations').status).toBe('not trained ML');
    expect(itemByLabel(items, 'Scoring / recommendations').detail).toContain('Do not call them a trained MIP ML model');
    expect(itemByLabel(items, 'Compliance posture').status).toBe('no certification claim');
    expect(itemByLabel(items, 'Audit coverage').detail).toContain('Do not claim every click');
  });
});
