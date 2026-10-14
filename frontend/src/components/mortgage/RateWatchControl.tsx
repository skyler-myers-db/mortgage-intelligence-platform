/**
 * "Watch this level" on the Rate Lever (audit 2026-09-21 `wow-ai-2`).
 *
 * deviation:watch-this-level: the prototype legend (design_files/Module 0
 * Prototype.html ~L1860-1871) is a static class bar with no slider and no
 * watch. At a non-zero step the lever offers one ghost button that saves a
 * reviewed watchlist of that step's par level; each run compares the weekly
 * 30-year print with it and drafts a review note only when it is reached.
 *
 * It ships in the lever's lazy rateScenario chunk (RateScenarioControl
 * imports it) and reads nothing on render, hover or prefetch. The click posts
 * the step only (`{step_bps, cadence, request_id}`): never a rate and never a
 * name, because the grid shown may be a persisted copy up to a day old, so the
 * server reads the level from today's grid and the success line shows the
 * SERVER's level. Pessimistic: the button is aria-disabled while the save is
 * in flight (focus stays on it) and nothing reads as watched until the server
 * answers. A 429 goes to the shell toast platform with a Retry behind the
 * Retry-After countdown. A new step remounts the control (the parent keys it).
 */
import { QueryClientContext } from '@tanstack/react-query';
import { useContext, useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router';
import { CLIENT_FAILURE_MESSAGES } from '../../lib/apiFailure';
import { growthAgentRateWatchApi } from '../../lib/apiClients/growthAgentRateWatch';
import { ApiError, _newRequestId, clientFailureReason } from '../../lib/apiTransport';
import { ratePct } from '../../lib/formatters';
import { SCHEDULED_RUN_STATUS } from '../../lib/growthAgentSchedulerCopy';
import { queryKeys } from '../../lib/queryKeys';
import { toast } from '../../lib/toast';
import type { GrowthAgentRateWatchResponse } from '../../types/growthAgent';

export const WATCH_DESCRIPTION =
  'Saves a watchlist that checks the weekly 30-year print against this level and drafts a review note when it is reached. Nothing is sent.';
/** Any 4xx but a 429: the grid changed or the level is not watchable; nothing was saved. */
export const WATCH_REFUSED_MESSAGE = "This level can't be watched as shown. Move the slider and try again.";
/** A server or unknown failure. A retry is safe: an identical watch is returned as-is. */
export const WATCH_FAILED_MESSAGE = "Couldn't save the watch right now. Try again shortly.";
export const SAVED_MONITORS_HREF = '/ask-genie?tab=monitors';

type WatchState =
  | { status: 'idle' }
  | { status: 'pending' }
  | { status: 'saved'; saved: GrowthAgentRateWatchResponse }
  | { status: 'failed'; message: string };

const IDLE: WatchState = { status: 'idle' };

/** Lender copy for a failed save; never the error's own text (a 422 body names schema fields). */
export function watchFailureMessage(error: unknown): string {
  const client = clientFailureReason(error);
  if (client) return CLIENT_FAILURE_MESSAGES[client];
  if (error instanceof ApiError && error.status !== null && error.status >= 400 && error.status < 500) {
    return WATCH_REFUSED_MESSAGE;
  }
  return WATCH_FAILED_MESSAGE;
}

interface WatchHandlers {
  onSaved: (response: GrowthAgentRateWatchResponse) => void;
  onFailed: (message: string) => void;
  onThrottled: () => void;
}

/**
 * One save of the watch for `step`. Module-level (not a closure in the
 * component) so the 429 toast's Retry can post the same step again without
 * the component's function referring to itself, which the React Compiler
 * cannot compile.
 */
function postWatch(step: number, handlers: WatchHandlers): void {
  growthAgentRateWatchApi.saveRateWatch({ step_bps: step, cadence: 'daily', request_id: _newRequestId() }).then(
    handlers.onSaved,
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 429) {
        handlers.onThrottled();
        toast.info("Couldn't save the watch yet", {
          detail: 'Too many requests. Retry when the countdown ends.',
          action: { label: 'Retry', onAction: () => postWatch(step, handlers) },
          retryAfterMs: error.retryAfterMs,
        });
        return;
      }
      handlers.onFailed(watchFailureMessage(error));
    },
  );
}

export default function RateWatchControl({ step, levelPct }: { step: number; levelPct: number }) {
  const descriptionId = useId();
  const statusRef = useRef<HTMLSpanElement>(null);
  // Provider-optional (the lever renders in tests without one): the summary refresh is best-effort.
  const queryClient = useContext(QueryClientContext);
  const [state, setState] = useState<WatchState>(IDLE);
  const saved = state.status === 'saved' ? state.saved : null;

  useEffect(() => {
    if (saved) statusRef.current?.focus();
  }, [saved]);

  function save() {
    setState({ status: 'pending' });
    postWatch(step, {
      onSaved: (response) => {
        setState({ status: 'saved', saved: response });
        void queryClient?.invalidateQueries({ queryKey: queryKeys.growthAgentWatchlistSummary() });
      },
      onFailed: (message) => setState({ status: 'failed', message }),
      onThrottled: () => setState(IDLE),
    });
  }

  if (saved) {
    const print = saved.latest_print_pct;
    // One line of text that wraps like text (no button row), so the watching
    // state is never taller than the button state by more than one line.
    return (
      <span className="rate-lever__watching-line">
        <span ref={statusRef} className="rate-lever__watching" role="status" tabIndex={-1}>
          Watching {ratePct(saved.watch_level_pct)} · {SCHEDULED_RUN_STATUS[saved.scheduler.state]}
          {print !== null ? ` · Latest print ${ratePct(print)}` : ''}
        </span>{' '}
        <Link to={SAVED_MONITORS_HREF} className="rate-lever__watching-link">
          Saved monitors
        </Link>
      </span>
    );
  }
  const pending = state.status === 'pending';
  return (
    <>
      <button
        type="button"
        className="btn btn--ghost btn--sm rate-lever__watch"
        aria-describedby={descriptionId}
        aria-disabled={pending || undefined}
        onClick={() => {
          if (!pending) save();
        }}
      >
        {pending ? 'Saving…' : `Watch ${ratePct(levelPct)}`}
      </button>
      <span id={descriptionId} className="sr-only">
        {WATCH_DESCRIPTION}
      </span>
      {state.status === 'failed' && (
        <span className="rate-lever__watch-error" role="alert">
          {state.message}
        </span>
      )}
    </>
  );
}
