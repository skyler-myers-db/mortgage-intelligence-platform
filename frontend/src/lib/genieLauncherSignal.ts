import { isGenieRouteAskVisible } from '../components/mortgage/useGenieAnnouncer';
import { GENIE_CONVERSATION_RESET_EVENT } from './genieConversation';
import {
  getGenieTurnSnapshot,
  subscribeGenieTurn,
  subscribeGenieTurnSettled,
  type GenieTurnSettledEvent,
} from './genieInFlightTurn';
import {
  isGenieLauncherStatusClaimed,
  setGenieTurnStatus,
  type GenieLauncherOutcome,
  type GenieTurnStatus,
} from './genieTurnStatus';

export { resumeGenieTurnFromSession } from './genieInFlightTurn';

/**
 * The launcher signal before the floating panel's first mount (audit
 * 2026-09-21 `genie-02` item 2, Genie residual #2). The mounted panel drives
 * lib/genieTurnStatus itself; until the user first opens it, nothing did, so
 * a turn a reload resumed, or one started on /ask-genie, left the topbar
 * toggle and the `.genie__fab` without a ring or an answer-ready badge.
 *
 * A lazy module: the shell imports it only when sessionStorage holds a turn
 * to resume (GenieDock), and /ask-genie when it mounts. `ensure` is an
 * idempotent singleton that follows the in-flight store and writes the
 * signal only while no panel holds the claim:
 *   - 'running' only for a REVEALED turn: a resumed turn shows no ring until
 *     its first 200 under the current identity (fail-closed, like its
 *     question);
 *   - 'ready' plus its outcome when a turn settles out of sight (the route's
 *     Ask tab not shown); a settle in sight is idle;
 *   - 'idle' on a conversation reset, and when a turn ends with no settle
 *     (Stop, an interruption, a fail-closed clear).
 * It never mounts the chat and never calls /api/genie/start.
 */

let unsubscribe: (() => void) | null = null;
/** The generation of the in-flight turn last seen, until it ends. */
let tracked: number | null = null;

function write(next: GenieTurnStatus, outcome?: GenieLauncherOutcome): void {
  if (isGenieLauncherStatusClaimed()) return;
  setGenieTurnStatus(next, outcome);
}

function onTurn(): void {
  const { inFlight } = getGenieTurnSnapshot();
  if (inFlight) {
    tracked = inFlight.generation;
    if (inFlight.revealed) write('running');
    return;
  }
  if (tracked === null) return;
  // Ended with no settle: Stop, an interruption or a fail-closed clear.
  tracked = null;
  write('idle');
}

function onSettled(event: GenieTurnSettledEvent): void {
  // The in-flight clear that follows is this settle, not an abandoned turn.
  tracked = null;
  if (isGenieRouteAskVisible()) write('idle');
  else write('ready', event.outcome);
}

function onReset(): void {
  tracked = null;
  write('idle');
}

export function ensureGenieLauncherSignal(): void {
  if (unsubscribe) return;
  const offTurn = subscribeGenieTurn(onTurn);
  const offSettled = subscribeGenieTurnSettled(onSettled);
  window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, onReset);
  unsubscribe = () => {
    offTurn();
    offSettled();
    window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, onReset);
  };
  onTurn();
}

export function __resetGenieLauncherSignalForTests(): void {
  unsubscribe?.();
  unsubscribe = null;
  tracked = null;
}
