import { Suspense, useEffect, useState, type ComponentType } from 'react';
import { subscribeGenieOpenRequests } from '../../lib/genieOpen';
import { Icon } from '../Icon';

interface GenieDockProps {
  open: boolean;
  onOpen: () => void;
  /** Warm the lazy chat chunk on launcher hover/focus. */
  onWarm: () => void;
  /** The lazy floating-chat component (owned by AppShell with its preloader). */
  Chat: ComponentType;
}

/**
 * Mount point for the floating Genie chat (audit 2026-09-21 `runtime-01` /
 * `genie-02`).
 *
 * The shell used to render `{genieOpen ? <LazyGenieChat /> : null}`: every
 * close unmounted the chat, and the chat aborted its in-flight ask on unmount.
 * Genie turns take 30-200 seconds and the panel covers the page, so users
 * close it — which silently destroyed the turn, the question and the answer.
 *
 * Now the chat mounts on FIRST open and is never unmounted again. Closing only
 * hides the panel (`.genie:not(.is-open)`), so the turn keeps running.
 * Mounting stays lazy on purpose: before the first open nothing loads the chat
 * chunk or calls `/api/genie/start`.
 *
 * Launcher: the chat renders its own `.genie__fab` (with the running ring and
 * answer-ready badge), so the shell's FAB exists only until the chat has
 * mounted. Rendering both would put two launchers on the same spot.
 *
 * Open requests (audit `genie-04`): `openGenie({ prompt })` from any surface
 * reaches the shell here as a window event and opens the panel; the queued
 * prefill is consumed by the chat once it is open. Nothing is submitted.
 */
export function GenieDock({ open, onOpen, onWarm, Chat }: GenieDockProps) {
  const [everOpened, setEverOpened] = useState(open);
  // Latch during render (the documented "adjust state when a prop changes"
  // pattern) so the chat mounts in the same commit the panel first opens.
  if (open && !everOpened) setEverOpened(true);
  useEffect(() => subscribeGenieOpenRequests(onOpen), [onOpen]);

  return (
    <>
      {!everOpened && (
        <button
          className="genie__fab"
          onClick={onOpen}
          onMouseEnter={onWarm}
          onFocus={onWarm}
          aria-label="Open Genie"
          type="button"
        >
          <Icon name="sparkle" size={22} />
        </button>
      )}
      <Suspense fallback={null}>{everOpened ? <Chat /> : null}</Suspense>
    </>
  );
}
