/**
 * Print-lane fixtures (W5d, responsive-08): the PRINT_EXPORT receipt an
 * Export PDF waits for. The default handler answers the request it is sent
 * (Borrower 360: the masked id and one row; the audit ledger: a keyed
 * fingerprint and the page's rows), so a spec can assert what reached the
 * wire; a spec that holds, fails or counts the receipt registers its own.
 *
 * Synthetic only: masked `B-` ids, `summit-mortgage.example` staff addresses.
 */
import type { PrintReceipt, PrintReceiptRequest } from '../../../../src/lib/apiClients/printReceipt';
import type { ContractSample } from '../contractSamples';
import { fixture, json, type FixtureEntry } from '../mockApi';
import { PRIMARY_BORROWER } from './borrowers';

export const PRINT_RECEIPT_ID = '9d4e1c2a-7b3f-4e8a-b1c6-00000000c0de';
export const PRINT_ACTOR = 'approver@summit-mortgage.example';
export const PRINT_RECORDED_AT = '2026-07-14T15:30:00Z';

const DEFAULT_REQUEST: PrintReceiptRequest = {
  surface: 'borrower_360',
  borrower_id: PRIMARY_BORROWER.borrower_id,
  row_count: 1,
};

/** The receipt the server answers for `request` (any body falls back to a dossier print). */
export function printReceiptFor(body: unknown): PrintReceipt {
  const request = (body && typeof body === 'object' ? body : DEFAULT_REQUEST) as PrintReceiptRequest;
  const ledger = request.surface === 'audit_ledger';
  return {
    audit_event_id: PRINT_RECEIPT_ID,
    event_type: 'PRINT_EXPORT',
    actor: PRINT_ACTOR,
    surface: ledger ? 'audit_ledger' : 'borrower_360',
    row_count: typeof request.row_count === 'number' ? request.row_count : 1,
    borrower_id: ledger ? null : request.borrower_id ?? PRIMARY_BORROWER.borrower_id,
    filter_fingerprint: ledger ? 'a'.repeat(64) : null,
    recorded_at: PRINT_RECORDED_AT,
  };
}

export const printReceiptFixtures: FixtureEntry[] = [
  fixture('POST', '/api/print-receipt', ({ body }) => json<PrintReceipt>(printReceiptFor(body))),
];

export function contractSamples(): ContractSample[] {
  return [
    {
      source: 'data/printReceipt.ts#borrower_360',
      method: 'POST',
      pattern: '/api/print-receipt',
      body: printReceiptFor(DEFAULT_REQUEST),
    },
    {
      source: 'data/printReceipt.ts#audit_ledger',
      method: 'POST',
      pattern: '/api/print-receipt',
      body: printReceiptFor({ surface: 'audit_ledger', filters: { event_type: 'APPROVE' }, row_count: 25 }),
    },
  ];
}
