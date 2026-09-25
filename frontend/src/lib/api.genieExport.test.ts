import { afterEach, describe, expect, it, vi } from 'vitest';
import { postGenieExportReceipt, type GenieAnswerExportReceiptRequest } from './apiClients/genieExport';

/**
 * Genie answer export receipt client (audit 2026-09-21 `genie-06`, slice 2):
 * one POST of exactly the declaration to the canonical receipt path, and a
 * refusal is never retried (one click is one ledger attempt).
 */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const DECLARATION: GenieAnswerExportReceiptRequest = {
  conversation_id: 'conv-0001',
  message_id: 'msg-0001',
  scope: 'answer',
  row_count: 120,
  answer_row_count: 120,
  csv_sha256: 'a'.repeat(64),
  columns_sha256: 'b'.repeat(64),
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Genie export receipt client', () => {
  it('posts exactly the declaration to /api/v1/genie/export-receipt and returns the ledger row', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, {
        audit_event_id: 'evt-1',
        event_type: 'GENIE_ANSWER_EXPORT',
        actor: 'analyst@summit-mortgage.example',
        scope: 'answer',
        row_count: 120,
        csv_sha256: 'a'.repeat(64),
        columns_sha256: 'b'.repeat(64),
        recorded_at: '2026-09-21T00:00:00Z',
      });
    });

    const receipt = await postGenieExportReceipt(DECLARATION);

    expect(receipt.audit_event_id).toBe('evt-1');
    expect(calls).toHaveLength(1);
    expect(new URL(calls[0].path, 'http://localhost').pathname).toBe('/api/v1/genie/export-receipt');
    expect(calls[0].init?.method).toBe('POST');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual(DECLARATION);
  });

  it.each([404, 422])('does not retry a %s refusal', async (status) => {
    let attempts = 0;
    vi.stubGlobal('fetch', async () => {
      attempts += 1;
      return jsonResponse(status, { detail: 'Genie answer not found' });
    });

    await expect(postGenieExportReceipt(DECLARATION)).rejects.toMatchObject({ status });
    expect(attempts).toBe(1);
  });
});
