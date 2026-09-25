// Shell-initial, rendered at most once per document: compiler memo caches
// would only add initial-chunk bytes (see DegradedBanner.tsx).
'use no memo';

import { useCallback, useId, useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import { Icon } from '../Icon';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import {
  getSessionStatus,
  subscribeSessionStatus,
  type SessionStatusSnapshot,
  type UnrecordedWrite,
} from '../../lib/sessionStatus';

/**
 * SessionExpiredDialog — the ONE blocking surface for an ended Databricks
 * Apps session (audit 2026-09-21 `states-02`, `critic-v2`, `shell-v1`).
 *
 * Before: a lapsed session read as a warehouse outage. Every panel showed its
 * own "Failed to fetch" or a JSON SyntaxError, and the only hint that a
 * reload would fix it was a tooltip on the Topbar's amber pill.
 *
 * Now the fetch core records the expiry (lib/sessionStatus) and this modal
 * says so once, over everything. Reload keeps the current URL
 * (`location.reload()`), so the proxy's sign-in returns the user to the same
 * page. When a write failed on expiry, the dialog says it was NOT recorded:
 * an approval must never look like it went through.
 *
 * It cannot be dismissed: Escape is consumed by the shared Escape stack
 * (useFocusTrap) and the native `cancel`, and if the browser force-closes the
 * dialog anyway it re-opens, because nothing behind it can work until reload.
 *
 * Styles: `.session-dialog` in design-system/components/31-session-recovery.css.
 * It ships in the initial stylesheet on purpose: once the session ends the
 * proxy answers every lazy chunk with a sign-in redirect, so this surface
 * cannot be code-split.
 */

const UNRECORDED_COPY: Record<UnrecordedWrite, string> = {
  approval: 'Your approval was not recorded. Approve it again after you sign in.',
  rejection: 'Your rejection was not recorded. Reject it again after you sign in.',
  change: 'Your last change was not recorded. Make it again after you sign in.',
  bulk_approval: 'Your bulk approval stopped part-way. Rows already approved stay approved; the rest were not recorded. After you sign in, check Recent activity before approving them again.',
};

function reloadPage(): void {
  window.location.reload();
}

function noop(): void {
  // Escape is consumed, never acted on: the dialog is blocking.
}

function useSessionStatus(): SessionStatusSnapshot {
  return useSyncExternalStore(subscribeSessionStatus, getSessionStatus, getSessionStatus);
}

export function SessionExpiredDialog({ onReload = reloadPage }: { onReload?: () => void } = {}) {
  const status = useSessionStatus();
  if (!status.expired) return null;
  return <OpenSessionExpiredDialog unrecorded={status.unrecorded} onReload={onReload} />;
}

function OpenSessionExpiredDialog({
  unrecorded,
  onReload,
}: {
  unrecorded: UnrecordedWrite | null;
  onReload: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const reloadRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const bodyId = useId();

  const show = useCallback(() => {
    const dialog = dialogRef.current;
    if (!dialog || dialog.open) return;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }, []);

  useLayoutEffect(() => {
    show();
  }, [show]);

  useFocusTrap({ open: true, containerRef: dialogRef, initialFocusRef: reloadRef, onClose: noop });

  return (
    <dialog
      ref={dialogRef}
      className="session-dialog"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      data-session-dialog=""
      onCancel={(event) => event.preventDefault()}
      onClose={show}
    >
      <div className="session-dialog__ico" aria-hidden="true">
        <Icon name="shield" size={16} />
      </div>
      <div className="session-dialog__body">
        <h2 className="session-dialog__title" id={titleId}>Your session ended</h2>
        <div id={bodyId}>
          <p className="session-dialog__sub">Reload to sign in. You will come back to this page.</p>
          {unrecorded && (
            <p className="session-dialog__warn" data-session-unrecorded={unrecorded}>
              {UNRECORDED_COPY[unrecorded]}
            </p>
          )}
        </div>
      </div>
      <div className="session-dialog__actions">
        <button ref={reloadRef} type="button" className="btn btn--primary" onClick={onReload}>
          Reload
        </button>
      </div>
    </dialog>
  );
}
