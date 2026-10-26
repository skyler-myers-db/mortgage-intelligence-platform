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
 *
 * Fix round 1 pins the page identity: a receipt belongs to the page it was
 * requested on. A receipt that resolves after the page changed (a pager step
 * to another borrower, an unmount on a route change) prints nothing, and a
 * 429 Try again after the page changed asks for nothing.
 *
 * Fix round 2 pins one receipt, one paper: the caption lasts until THAT
 * print dialog's afterprint, so a later Ctrl/Cmd-P (which wrote no
 * PRINT_EXPORT row) prints as unaudited. Mutation check (lane report):
 * registering the afterprint clear after `window.print()` returns fails
 * "clears the caption when afterprint fires inside print(), as Chromium
 * does"; dropping the clear fails both one-shot cases.
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
  toastReceiptFailure: vi.fn(),
}));

vi.mock('../../lib/apiClients/printReceipt', () => ({ createPrintReceipt: mocks.createPrintReceipt }));
vi.mock('../../lib/toast', () => ({
  toast: { info: mocks.toastInfo, error: mocks.toastError, success: vi.fn() },
}));
vi.mock('./receiptFailureToast', () => ({ toastReceiptFailure: mocks.toastReceiptFailure }));
vi.mock('../AppContext', () => ({ useApp: () => ({ lender: 'Summit Mortgage' }) }));

import { PrintProvenance, UNAUDITED_PRINT } from './PrintProvenance';
import {
  PRINT_NOT_RECORDED,
  PRINT_PAGE_CHANGED,
  PRINT_PAGE_CHANGED_RECORDED,
  PRINT_PAGE_CHANGED_UNRECORDED,
  PRINT_RECEIPT_RECORDED,
  usePrintReceipt,
} from './usePrintReceipt';

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

const OTHER: PrintReceiptRequest = { ...REQUEST, borrower_id: 'B-0000000000002' };

function Harness({ request = REQUEST, pageKey }: { request?: PrintReceiptRequest; pageKey?: string }) {
  const print = usePrintReceipt(request, pageKey);
  return (
    <>
      <button type="button" id="export" onClick={print.run} aria-busy={print.pending || undefined}>
        Export PDF
      </button>
      <PrintProvenance subject={`Borrower 360 · ${request.borrower_id}`} receipt={print.receipt} />
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
    expect(mocks.toastReceiptFailure).toHaveBeenCalledWith(PRINT_NOT_RECORDED, failure, 'the print receipt', expect.any(Function));
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);
  });

  it('a 429 never prints; the failure toast Try again asks for the receipt again', async () => {
    const limited = new ApiError('rate limited', { path: '/api/print-receipt', status: 429, retryAfterMs: 4000 });
    mocks.createPrintReceipt.mockRejectedValueOnce(limited);

    click();
    await act(async () => undefined);

    expect(window.print).not.toHaveBeenCalled();
    const [title, error, , retry] = mocks.toastReceiptFailure.mock.calls[0] as [string, unknown, string, () => void];
    expect(title).toBe(PRINT_NOT_RECORDED);
    expect(error).toBe(limited);

    mocks.createPrintReceipt.mockResolvedValueOnce(RECEIPT);
    await act(async () => retry());
    expect(mocks.createPrintReceipt).toHaveBeenCalledTimes(2);
    expect(window.print).toHaveBeenCalledTimes(1);
  });

  it('a receipt for another request does not caption this page', async () => {
    mocks.createPrintReceipt.mockResolvedValue(RECEIPT);
    click();
    await act(async () => undefined);
    expect(provenance()?.textContent).toContain('Audit receipt');
    act(() => root.render(<Harness request={OTHER} />));
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);
  });

  it('a receipt for another page key (same request) does not caption this page', async () => {
    act(() => root.render(<Harness pageKey="0|evt-1,evt-2" />));
    mocks.createPrintReceipt.mockResolvedValue(RECEIPT);
    click();
    await act(async () => undefined);
    expect(provenance()?.textContent).toContain('Audit receipt');
    act(() => root.render(<Harness pageKey="1|evt-3,evt-4" />));
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);
  });

  it('a receipt that resolves after the page changed prints nothing and says the page changed', async () => {
    const receipt = deferred<PrintReceipt>();
    mocks.createPrintReceipt.mockReturnValue(receipt.promise);
    click();
    expect(mocks.createPrintReceipt).toHaveBeenCalledWith(REQUEST);

    // The Borrower 360 pager keeps the route instance: B replaces A in place.
    act(() => root.render(<Harness request={OTHER} />));
    await act(async () => receipt.resolve(RECEIPT));

    expect(window.print).not.toHaveBeenCalled();
    expect(mocks.toastInfo).not.toHaveBeenCalledWith(PRINT_RECEIPT_RECORDED, expect.anything());
    expect(mocks.toastInfo).toHaveBeenCalledWith(PRINT_PAGE_CHANGED, {
      detail: PRINT_PAGE_CHANGED_RECORDED,
      auditEventId: RECEIPT.audit_event_id,
    });
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);
    expect(container.querySelector('#export')?.getAttribute('aria-busy')).toBeNull();
  });

  it('a receipt that resolves after an unmount (a route change) prints nothing', async () => {
    const receipt = deferred<PrintReceipt>();
    mocks.createPrintReceipt.mockReturnValue(receipt.promise);
    click();
    act(() => root.render(<p id="elsewhere">Another route</p>));
    await act(async () => receipt.resolve(RECEIPT));

    expect(window.print).not.toHaveBeenCalled();
    expect(mocks.toastInfo).toHaveBeenCalledWith(PRINT_PAGE_CHANGED, {
      detail: PRINT_PAGE_CHANGED_RECORDED,
      auditEventId: RECEIPT.audit_event_id,
    });
  });

  it("a 429 Try again after the page changed asks for nothing and never prints the new page under A's receipt", async () => {
    const limited = new ApiError('rate limited', { path: '/api/print-receipt', status: 429, retryAfterMs: 4000 });
    mocks.createPrintReceipt.mockRejectedValueOnce(limited);
    click();
    await act(async () => undefined);
    const retry = (mocks.toastReceiptFailure.mock.calls[0] as [string, unknown, string, () => void])[3];

    act(() => root.render(<Harness request={OTHER} />));
    mocks.createPrintReceipt.mockResolvedValue(RECEIPT);
    await act(async () => retry());

    expect(mocks.createPrintReceipt).toHaveBeenCalledTimes(1);
    expect(window.print).not.toHaveBeenCalled();
    expect(mocks.toastInfo).toHaveBeenCalledWith(PRINT_PAGE_CHANGED, { detail: PRINT_PAGE_CHANGED_UNRECORDED });
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);
  });

  it('a 429 Try again whose receipt resolves after the page changed still prints nothing', async () => {
    const limited = new ApiError('rate limited', { path: '/api/print-receipt', status: 429, retryAfterMs: 4000 });
    mocks.createPrintReceipt.mockRejectedValueOnce(limited);
    click();
    await act(async () => undefined);
    const retry = (mocks.toastReceiptFailure.mock.calls[0] as [string, unknown, string, () => void])[3];

    const receipt = deferred<PrintReceipt>();
    mocks.createPrintReceipt.mockReturnValueOnce(receipt.promise);
    await act(async () => retry());
    expect(mocks.createPrintReceipt).toHaveBeenLastCalledWith(REQUEST);
    act(() => root.render(<Harness request={OTHER} />));
    await act(async () => receipt.resolve(RECEIPT));

    expect(window.print).not.toHaveBeenCalled();
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);
  });
});

describe('usePrintReceipt: one receipt, one paper', () => {
  it('keeps the caption until its dialog closes; a later Ctrl/Cmd-P of the same page prints as unaudited', async () => {
    mocks.createPrintReceipt.mockResolvedValue(RECEIPT);
    click();
    await act(async () => undefined);

    expect(printed).toEqual([expect.stringContaining(`Audit receipt ${RECEIPT.audit_event_id}`)]);
    expect(provenance()?.textContent).toContain(`Audit receipt ${RECEIPT.audit_event_id}`);

    // The Export PDF dialog closes.
    await act(async () => {
      window.dispatchEvent(new Event('afterprint'));
    });
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);

    // Ctrl/Cmd-P on the same page: no receipt was asked for, none is claimed.
    act(() => window.print());
    expect(printed[1]).toContain(UNAUDITED_PRINT);
    expect(printed[1]).not.toContain(RECEIPT.audit_event_id);
    expect(mocks.createPrintReceipt).toHaveBeenCalledTimes(1);
  });

  it('clears the caption when afterprint fires inside print(), as Chromium does', async () => {
    // Chromium's print() blocks and dispatches afterprint before returning.
    Object.defineProperty(window, 'print', {
      configurable: true,
      writable: true,
      value: vi.fn(() => {
        printed.push(provenance()?.textContent ?? '');
        window.dispatchEvent(new Event('afterprint'));
      }),
    });
    mocks.createPrintReceipt.mockResolvedValue(RECEIPT);
    click();
    await act(async () => undefined);

    expect(printed).toEqual([expect.stringContaining(`Audit receipt ${RECEIPT.audit_event_id}`)]);
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);
  });

  it('a second Export PDF captions its own print, and its afterprint clears it again', async () => {
    const second: PrintReceipt = { ...RECEIPT, audit_event_id: '0b1c2d3e-4f50-4617-8293-a4b5c6d7e8f9' };
    mocks.createPrintReceipt.mockResolvedValueOnce(RECEIPT).mockResolvedValueOnce(second);
    click();
    await act(async () => undefined);
    await act(async () => {
      window.dispatchEvent(new Event('afterprint'));
    });
    click();
    await act(async () => undefined);

    expect(printed[1]).toContain(`Audit receipt ${second.audit_event_id}`);
    await act(async () => {
      window.dispatchEvent(new Event('afterprint'));
    });
    expect(provenance()?.textContent).toContain(UNAUDITED_PRINT);
  });

  it('drops the pending afterprint listener on unmount', async () => {
    mocks.createPrintReceipt.mockResolvedValue(RECEIPT);
    click();
    await act(async () => undefined);
    const removed = vi.spyOn(window, 'removeEventListener');
    act(() => root.render(<p id="elsewhere">Another route</p>));
    expect(removed).toHaveBeenCalledWith('afterprint', expect.any(Function));
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
