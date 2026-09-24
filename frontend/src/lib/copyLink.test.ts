import { afterEach, describe, expect, it, vi } from 'vitest';
import { copyLink } from './copyLink';
import { clearToasts, getToasts } from './toast';

const MESSAGES = {
  success: 'Queue link copied',
  successDetail: 'Left out: the open row.',
  failure: 'Copy failed',
  failureDetail: 'The browser blocked clipboard access. Copy the address bar instead.',
};

function stubClipboard(writeText: ((text: string) => Promise<void>) | null): void {
  vi.stubGlobal('navigator', writeText ? { clipboard: { writeText } } : {});
}

afterEach(() => {
  clearToasts();
  vi.unstubAllGlobals();
});

describe('copyLink (audit tables-09)', () => {
  it('writes exactly the URL it was given and raises the success toast', async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined);
    stubClipboard(writeText);

    await expect(copyLink('https://app.example/lead-queue?state=IL', MESSAGES)).resolves.toBe(true);

    expect(writeText).toHaveBeenCalledWith('https://app.example/lead-queue?state=IL');
    expect(getToasts()).toMatchObject([{ tone: 'success', title: 'Queue link copied', detail: 'Left out: the open row.' }]);
  });

  it('never throws when the clipboard refuses: the failure toast says so', async () => {
    stubClipboard(() => Promise.reject(new DOMException('denied', 'NotAllowedError')));

    await expect(copyLink('https://app.example/lead-queue', MESSAGES)).resolves.toBe(false);

    expect(getToasts()).toMatchObject([{ tone: 'error', title: 'Copy failed', detail: MESSAGES.failureDetail }]);
  });

  it('never throws when there is no Clipboard API at all', async () => {
    stubClipboard(null);

    await expect(copyLink('https://app.example/lead-queue', MESSAGES)).resolves.toBe(false);

    expect(getToasts()).toMatchObject([{ tone: 'error', title: 'Copy failed' }]);
  });

  it('puts the URL in no toast text', async () => {
    stubClipboard(vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined));
    await copyLink('https://app.example/lead-queue?assigned_to=me', MESSAGES);
    expect(JSON.stringify(getToasts())).not.toContain('https://');
  });
});
