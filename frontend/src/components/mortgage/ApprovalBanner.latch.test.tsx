/**
 * @vitest-environment happy-dom
 *
 * ApprovalBanner's synchronous double-click latch, in a real DOM (audit
 * runtime-03). The guard was rewritten without a try statement so React
 * Compiler compiles the component; the latch must behave exactly as before:
 * a second click while the first handler is pending fires nothing, and the
 * latch opens again once that handler resolves OR rejects.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApprovalBanner } from './ApprovalBanner';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type RejectionListener = (reason: unknown) => void;
const nodeProcess = (globalThis as unknown as {
  process: { on(event: string, listener: RejectionListener): void; off(event: string, listener: RejectionListener): void };
}).process;

function deferred() {
  let resolve: () => void = () => undefined;
  let reject: (error: Error) => void = () => undefined;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('ApprovalBanner double-click latch', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  const approveButton = () => [...document.querySelectorAll('button')].find((b) => b.textContent?.includes('Approve outreach')) as HTMLButtonElement;
  const rejectButton = () => [...document.querySelectorAll('button')].find((b) => b.textContent?.includes('Reject')) as HTMLButtonElement;

  it('fires one handler for a double click and re-opens once it resolves', async () => {
    const first = deferred();
    const onApprove = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(undefined);
    act(() => root.render(<ApprovalBanner count={1} onApprove={onApprove} />));

    await act(async () => {
      approveButton().click();
      approveButton().click();
    });
    expect(onApprove).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve();
      await first.promise;
    });
    await act(async () => {
      approveButton().click();
    });
    expect(onApprove).toHaveBeenCalledTimes(2);
  });

  it('shares one latch across Approve and Reject', async () => {
    const pending = deferred();
    const onApprove = vi.fn().mockReturnValue(pending.promise);
    const onReject = vi.fn();
    act(() => root.render(<ApprovalBanner count={1} onApprove={onApprove} onReject={onReject} />));

    await act(async () => {
      approveButton().click();
      rejectButton().click();
    });
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onReject).not.toHaveBeenCalled();
    await act(async () => {
      pending.resolve();
      await pending.promise;
    });
  });

  it('re-opens the latch when the handler throws or rejects', async () => {
    // The failure still rejects out of the click, as it did before the
    // rewrite; this test owns those two expected rejections.
    const expected = vi.fn();
    nodeProcess.on('unhandledRejection', expected);
    const onApprove = vi.fn()
      .mockImplementationOnce(() => {
        throw new Error('sync failure');
      })
      .mockRejectedValueOnce(new Error('async failure'))
      .mockResolvedValue(undefined);
    act(() => root.render(<ApprovalBanner count={1} onApprove={onApprove} />));

    for (let click = 0; click < 3; click += 1) {
      await act(async () => {
        approveButton().click();
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      });
    }
    expect(onApprove).toHaveBeenCalledTimes(3);
    nodeProcess.off('unhandledRejection', expected);
    expect(expected.mock.calls.map(([reason]) => (reason as Error).message)).toEqual(['sync failure', 'async failure']);
  });
});
