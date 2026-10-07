/**
 * @vitest-environment happy-dom
 *
 * Export PDF behind a server-owned receipt (audit responsive-08, W5d). The
 * defect class this pins: a print path that opens the dialog first and
 * records later (or never). The receipt must resolve BEFORE window.print(),
 * the provenance on the page must already name it when print() runs, a
 * failed receipt never prints, and a second click while one is pending is
 * ignored.
 *
 * Mutation check (lane report): calling `window.print()` before
 * `createPrintReceipt` resolves fails "prints only after the receipt
 * resolves, on the page that already names it".
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PrintReceipt, PrintReceiptRequest } from '../../lib/apiClients/printReceipt';
import { ApiError } from '../../lib/apiTransport';

const mocks = vi.hoisted(() => ({
  createPrintReceipt: vi.fn(),
  toastInfo: vi.fn(),
  toastError: vi.fn(),
  toastWriteFailure: vi.fn(),
}));

vi.mock('../../lib/apiClients/printReceipt', () => ({ createPrintReceipt: mocks.createPrintReceipt }));
vi.mock('../../lib/toast', () => ({
  toast: { info: mocks.toastInfo, error: mocks.toastError, success: vi.fn() },
}));
vi.mock('../mortgage/leadWriteFailureToast', () => ({ toastWriteFailure: mocks.toastWriteFailure }));
vi.mock('../AppContext', () => ({ useApp: () => ({ lender: 'Summit Mortgage' }) }));

import { PrintProvenance, UNAUDITED_PRINT } from './PrintProvenance';
import { PRINT_NOT_RECORDED, PRINT_RECEIPT_RECORDED, usePrintReceipt } from './usePrintReceipt';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const REQUEST: PrintReceiptRequest = { surface: 'borrower_360', borrower_id: 'B-0000000000001', row_count: 1 };
const RECEIPT: PrintReceipt = {
  audit_event_id: '6f2a9c1e-3b4d-4e5f-8a6b-7c8d9e0f1a2b',
  event_type: 'PRINT_EXPORT',
  actor: 'ledger.reader@summit.example',
  surface: 'borrower_360',
  row_count: 1,
  borrower_id: 'B-0000000000001',
  filter_fingerprint: null,
  recorded_at: '2026-10-07T15:00:04Z',
};

function Harness({ request = REQUEST }: { request?: PrintReceiptRequest }) {
  const print = usePrintReceipt();
  return (
    <>
      <button type="button" id="export" onClick={() => print.run(request)} aria-busy={print.pending || undefined}>
        Export PDF
      </button>
      <PrintProvenance subject="Borrower 360 · B-0000000000001" receipt={print.receiptFor(request)} />
    </>
  );
}

let container: HTMLDivElement;
let root: Root;
let printed: string[];

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const provenance = () => container.querySelector<HTMLElement>('[data-testid="print-provenance"]');
const click = () => act(() => container.querySelector<HTMLButtonElement>('#export')!.click());

beforeEach(() => {
  printed = [];
  Object.values(mocks).forEach((mock) => mock.mockReset());
  // What the paper would show at the moment the dialog opens (the test DOM
  // has no window.print of its own).
  Object.defineProperty(window, 'print', {
    configurable: true,
    writable: true,
    value: vi.fn(() => {
      printed.push(provenance()?.textContent ?? '');
    }),
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<Harness />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe('usePrintReceipt', () => {
  it('prints only after the receipt resolves, on the page that already names it', async () => {
    const receipt = deferred<PrintReceipt>();
    mocks.createPrintReceipt.mockReturnValue(receipt.promise);

    click();
    expect(mocks.createPrintReceipt).toHaveBeenCalledWith(REQUEST);
    expect(window.print).not.toHaveBeenCalled();
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);

    await act(async () => receipt.resolve(RECEIPT));

    expect(window.print).toHaveBeenCalledTimes(1);
    expect(printed[0]).toContain(`Audit receipt ${RECEIPT.audit_event_id}`);
    expect(printed[0]).toContain(`Exported by ${RECEIPT.actor}`);
    expect(mocks.toastInfo).toHaveBeenCalledWith(PRINT_RECEIPT_RECORDED, { auditEventId: RECEIPT.audit_event_id });
  });

  it('ignores a second click while the receipt is pending', async () => {
    const receipt = deferred<PrintReceipt>();
    mocks.createPrintReceipt.mockReturnValue(receipt.promise);
    click();
    click();
    expect(mocks.createPrintReceipt).toHaveBeenCalledTimes(1);
    await act(async () => receipt.resolve(RECEIPT));
    expect(window.print).toHaveBeenCalledTimes(1);
  });

  it('a failed receipt never prints and raises the described failure toast', async () => {
    const failure = new ApiError('service unavailable', { path: '/api/print-receipt', status: 503, dependency: 'lakebase' });
    mocks.createPrintReceipt.mockRejectedValue(failure);

    click();
    await act(async () => undefined);

    expect(window.print).not.toHaveBeenCalled();
    expect(mocks.toastWriteFailure).toHaveBeenCalledWith(PRINT_NOT_RECORDED, failure, 'the print receipt');
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);
  });

  it('a 429 never prints; its Try again waits out the Retry-After and asks again', async () => {
    const limited = new ApiError('rate limited', { path: '/api/print-receipt', status: 429, retryAfterMs: 4000 });
    mocks.createPrintReceipt.mockRejectedValueOnce(limited);

    click();
    await act(async () => undefined);

    expect(window.print).not.toHaveBeenCalled();
    expect(mocks.toastWriteFailure).not.toHaveBeenCalled();
    const [title, options] = mocks.toastError.mock.calls[0] as [string, { action: { label: string; onAction: () => void }; retryAfterMs: number }];
    expect(title).toBe(PRINT_NOT_RECORDED);
    expect(options.retryAfterMs).toBe(4000);
    expect(options.action.label).toBe('Try again');

    mocks.createPrintReceipt.mockResolvedValueOnce(RECEIPT);
    await act(async () => options.action.onAction());
    expect(mocks.createPrintReceipt).toHaveBeenCalledTimes(2);
    expect(window.print).toHaveBeenCalledTimes(1);
  });

  it('a receipt for another request does not caption this page', async () => {
    mocks.createPrintReceipt.mockResolvedValue(RECEIPT);
    click();
    await act(async () => undefined);
    expect(provenance()?.textContent).toContain('Audit receipt');
    act(() => root.render(<Harness request={{ ...REQUEST, borrower_id: 'B-0000000000002' }} />));
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);
  });
});

describe('PrintProvenance', () => {
  it('is hidden on screen, names the lender by text and never draws the lender mark', () => {
    const block = provenance();
    expect(block?.hidden).toBe(true);
    expect(block?.textContent).toContain('Borrower 360 · B-0000000000001 · Summit Mortgage');
    expect(block?.querySelector('img, svg')).toBeNull();
  });
});
