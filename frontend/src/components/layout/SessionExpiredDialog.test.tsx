// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { _resetSessionStatusForTests, markSessionExpired, markUnrecordedWrite } from '../../lib/sessionStatus';
import { SessionExpiredDialog } from './SessionExpiredDialog';

/**
 * The blocking "Your session ended" dialog (audit 2026-09-21 `states-02`,
 * `critic-v2`). Rendered for real; the fetch core's store is the only input.
 */

describe('SessionExpiredDialog', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    _resetSessionStatusForTests();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    _resetSessionStatusForTests();
  });

  async function render(onReload = vi.fn()) {
    await act(async () => {
      root.render(
        <>
          <button type="button">behind</button>
          <SessionExpiredDialog onReload={onReload} />
        </>,
      );
    });
    return onReload;
  }

  const dialog = () => container.querySelector<HTMLDialogElement>('dialog.session-dialog');

  it('renders nothing while the session is active', async () => {
    await render();
    expect(dialog()).toBeNull();
  });

  it('opens as a modal alertdialog with focus on Reload once the session ends, and Reload reloads', async () => {
    const onReload = await render();
    await act(async () => {
      markSessionExpired({ method: 'GET', path: '/api/v1/leads' });
    });
    const el = dialog();
    expect(el).not.toBeNull();
    expect(el?.open).toBe(true);
    expect(el?.getAttribute('role')).toBe('alertdialog');
    expect(el?.getAttribute('aria-modal')).toBe('true');
    const title = el?.querySelector(`#${CSS.escape(el.getAttribute('aria-labelledby') ?? '')}`);
    expect(title?.textContent).toBe('Your session ended');
    expect(el?.textContent).toContain('Reload to sign in.');
    expect(el?.querySelector('[data-session-unrecorded]'), 'no write failed, so no "not recorded" line').toBeNull();

    await act(async () => {
      await Promise.resolve();
    });
    const reload = el?.querySelector('button');
    expect(reload?.textContent).toBe('Reload');
    expect(document.activeElement).toBe(reload);
    reload?.click();
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it('says an in-progress approval was not recorded', async () => {
    await render();
    await act(async () => {
      markSessionExpired({ method: 'POST', path: '/api/v1/outreach/approve' });
    });
    const warn = dialog()?.querySelector('[data-session-unrecorded]');
    expect(warn?.getAttribute('data-session-unrecorded')).toBe('approval');
    expect(warn?.textContent).toBe('Your approval was not recorded. Approve it again after you sign in.');
  });

  it('says nothing was lost when only an outreach draft met the ended session (the Offer page loads one on open)', async () => {
    await render();
    await act(async () => {
      markSessionExpired({ method: 'POST', path: '/api/v1/outreach/draft' });
    });
    expect(dialog()?.open).toBe(true);
    expect(dialog()?.querySelector('[data-session-unrecorded]')).toBeNull();
  });

  it('says an Approve click that failed on its draft step was not recorded', async () => {
    await render();
    await act(async () => {
      markSessionExpired({ method: 'POST', path: '/api/v1/outreach/draft' });
      markUnrecordedWrite('approval');
    });
    expect(dialog()?.querySelector('[data-session-unrecorded]')?.textContent).toBe(
      'Your approval was not recorded. Approve it again after you sign in.',
    );
  });

  it('cannot be dismissed: Escape is cancelled and a forced close re-opens it', async () => {
    await render();
    await act(async () => {
      markSessionExpired({ method: 'GET', path: '/api/v1/leads' });
    });
    const el = dialog();
    const cancel = new Event('cancel', { cancelable: true });
    el?.dispatchEvent(cancel);
    expect(cancel.defaultPrevented).toBe(true);

    await act(async () => {
      el?.close();
      el?.dispatchEvent(new Event('close'));
    });
    expect(dialog()?.open).toBe(true);
  });
});
