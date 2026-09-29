import { Suspense, useEffect, useState, type ComponentType } from 'react';
import { GENIE_IN_FLIGHT_TURN_KEY } from '../../lib/genieConversation';
import { subscribeGenieOpenRequests } from '../../lib/genieOpen';
import {
  GENIE_LAUNCHER_STATUS_ID,
  genieLauncherStateClass,
  genieLauncherStatusText,
  useGenieLauncherOutcome,
  useGenieTurnStatus,
} from '../../lib/genieTurnStatus';
import { Icon } from '../Icon';
import { GenieBoundary } from './ShellPanelBoundaries';

interface GenieDockProps {
  open: boolean;
  onOpen: () => void;
  /** Hides the panel; the error frame's Close uses it (the chat has its own). */
  onClose: () => void;
  /** Warm the lazy chat chunk on launcher hover/focus. */
  onWarm: () => void;
  /** The lazy floating-chat component (owned by AppShell with its preloader). */
  Chat: ComponentType;
}

/** A reload left a Genie turn in this tab's session. Module level: a try
 *  statement in the component would stop the React Compiler. */
function hasGenieTurnToResume(): boolean {
  try {
    return window.sessionStorage.getItem(GENIE_IN_FLIGHT_TURN_KEY) !== null;
  } catch {
    return false;
  }
}

/** Resume that turn from the shell and let the launcher signal follow it.
 *  Lazy: the signal (and the store it follows) load only for a record. A
 *  failed chunk leaves the resume to the panel's first open. */
function resumeGenieTurnFromShell(): void {
  import('../../lib/genieLauncherSignal')
    .then((signal) => {
      signal.ensureGenieLauncherSignal();
      signal.resumeGenieTurnFromSession();
    })
    .catch(() => undefined);
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
 * Before the first open (audit `genie-02` item 2, Genie residual #2): a turn
 * a reload interrupted resumes from HERE, on whatever route the reload landed,
 * through the lazy launcher signal, without mounting the chat or calling
 * `/api/genie/start`. The shell FAB wears the same ring and badge classes as
 * the chat's (16-genie-fab-status.css covers any `.genie__fab`), and the dock
 * renders the launchers' sr-only `#genie-launcher-status` description until
 * the chat mounts and renders its own; the two never coexist.
 *
 * Open requests (audit `genie-04`): `openGenie({ prompt })` from any surface
 * reaches the shell here as a window event and opens the panel; the queued
 * prefill is consumed by the chat once it is open. Nothing is submitted.
 *
 * Crash containment (audit `states-01`): the chat sits inside GenieBoundary,
 * which is always rendered, so the tree never changes shape and the chat
 * re-mounts only on the surface's Try again. A throw or a failed chunk shows
 * the recovery surface inside a Genie frame whose Close hides the panel like
 * the chat's own; the dock (and its open-request subscription) stays mounted.
 */
export function GenieDock({ open, onOpen, onClose, onWarm, Chat }: GenieDockProps) {
  const [everOpened, setEverOpened] = useState(open);
  // Latch during render (the documented "adjust state when a prop changes"
  // pattern) so the chat mounts in the same commit the panel first opens.
  if (open && !everOpened) setEverOpened(true);
  useEffect(() => subscribeGenieOpenRequests(onOpen), [onOpen]);
  useEffect(() => {
    if (hasGenieTurnToResume()) resumeGenieTurnFromShell();
  }, []);
  const status = useGenieTurnStatus();
  const outcome = useGenieLauncherOutcome();

  return (
    <>
      {!everOpened && (
        <>
          <span id={GENIE_LAUNCHER_STATUS_ID} className="sr-only">
            {genieLauncherStatusText(status, outcome)}
          </span>
          <button
            className={['genie__fab', genieLauncherStateClass(status)].filter(Boolean).join(' ')}
            onClick={onOpen}
            onMouseEnter={onWarm}
            onFocus={onWarm}
            aria-label="Open Genie"
            aria-describedby={status === 'idle' ? undefined : GENIE_LAUNCHER_STATUS_ID}
            type="button"
          >
            <Icon name="sparkle" size={22} />
          </button>
        </>
      )}
      <GenieBoundary open={open} onOpen={onOpen} onClose={onClose}>
        <Suspense fallback={null}>{everOpened ? <Chat /> : null}</Suspense>
      </GenieBoundary>
    </>
  );
}
