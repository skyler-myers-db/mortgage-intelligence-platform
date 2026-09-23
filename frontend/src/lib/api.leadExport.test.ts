import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import {
  buildLeadExportDeclaration,
  leadExportFiltersFromQuery,
  leadExportScope,
  sha256Hex,
} from './apiClients/leadExport';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('lead export receipt client', () => {
  it('posts the declaration to the canonical receipt path and returns the ledger row', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, {
        audit_event_id: 'evt-1',
        event_type: 'LEAD_EXPORT',
        actor: 'approver@summit-mortgage.example',
        scope: 'selected',
        row_count: 1,
        csv_sha256: 'a'.repeat(64),
        borrower_ids_sha256: 'b'.repeat(64),
        filter_fingerprint: 'c'.repeat(64),
        recorded_at: '2026-09-21T00:00:00Z',
      });
    });

    const receipt = await api.leadExportReceipt({
      scope: 'selected',
      row_count: 1,
      csv_sha256: 'a'.repeat(64),
      borrower_ids: ['B-AAAAAAAAAAAA1'],
      borrower_ids_sha256: 'b'.repeat(64),
      filters: { states: 'IL' },
    });

    expect(receipt.audit_event_id).toBe('evt-1');
    expect(calls).toHaveLength(1);
    expect(new URL(calls[0].path, 'http://localhost').pathname).toBe('/api/v1/leads/export-receipt');
    expect(calls[0].init?.method).toBe('POST');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      scope: 'selected',
      row_count: 1,
      csv_sha256: 'a'.repeat(64),
      borrower_ids: ['B-AAAAAAAAAAAA1'],
      borrower_ids_sha256: 'b'.repeat(64),
      filters: { states: 'IL' },
    });
  });

  it('does not retry a non-retryable refusal, so one click is one ledger attempt', async () => {
    let attempts = 0;
    vi.stubGlobal('fetch', async () => {
      attempts += 1;
      return jsonResponse(422, { detail: 'export declaration does not match the borrower id list' });
    });

    await expect(
      api.leadExportReceipt({
        scope: 'loaded',
        row_count: 1,
        csv_sha256: 'a'.repeat(64),
        borrower_ids: ['B-AAAAAAAAAAAA1'],
        borrower_ids_sha256: 'b'.repeat(64),
        filters: {},
      }),
    ).rejects.toMatchObject({ status: 422 });
    expect(attempts).toBe(1);
  });
});

describe('lead export declaration', () => {
  it('hashes with SHA-256 hex (known vector)', async () => {
    expect(await sha256Hex('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
  });

  it('maps the export plan scope to the ledger token', () => {
    expect(leadExportScope({ scope: 'selected_rows' })).toBe('selected');
    expect(leadExportScope({ scope: 'loaded_rows' })).toBe('loaded');
  });

  it('turns the queue query string into the fingerprinted filter map', () => {
    expect(leadExportFiltersFromQuery('none')).toEqual({});
    expect(leadExportFiltersFromQuery(undefined)).toEqual({});
    expect(leadExportFiltersFromQuery('states=IL%2CTX&segment_codes=itm%2Cequity&segment_mode=any')).toEqual({
      states: 'IL,TX',
      segment_codes: 'itm,equity',
      segment_mode: 'any',
    });
    // Malformed names are dropped; a repeated key keeps its first value.
    expect(leadExportFiltersFromQuery('Bad-Key=1&states=IL&states=TX')).toEqual({ states: 'IL' });
  });

  it('declares the row count, ordered ids and both digests of the real inputs', async () => {
    const csv = 'borrower_id,city\nB-AAAAAAAAAAAA2,Chicago\nB-AAAAAAAAAAAA1,Chicago\n';
    const declaration = await buildLeadExportDeclaration(
      csv,
      { scope: 'selected_rows', rows: [{ borrower_id: 'B-AAAAAAAAAAAA2' }, { borrower_id: 'B-AAAAAAAAAAAA1' }] },
      'states=IL',
    );

    expect(declaration).toEqual({
      scope: 'selected',
      row_count: 2,
      csv_sha256: await sha256Hex(csv),
      borrower_ids: ['B-AAAAAAAAAAAA2', 'B-AAAAAAAAAAAA1'],
      borrower_ids_sha256: await sha256Hex('["B-AAAAAAAAAAAA2","B-AAAAAAAAAAAA1"]'),
      filters: { states: 'IL' },
    });
  });
});
