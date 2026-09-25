import type { Dispatch, RefObject, SetStateAction } from 'react';
import { onlineManager, type QueryClient } from '@tanstack/react-query';
import { isAbortError, type HealthPayload } from '../lib/api';
import type { HealthHint } from '../lib/apiTypes';
import { subscribeNetworkFailures } from '../lib/apiFailure';
import { isSessionExpired, subscribeSessionStatus } from '../lib/sessionStatus';
import { recoveredDependencies, refetchRecoveredQueries } from './healthRecovery';
import {
  nextConnection,
  refetchUnreachableQueries,
  type ConnectionStatus,
  type ConnectionTracker,
} from './connectionState';

/**
 * The shared `/api/health` poll loop, moved verbatim out of HealthProvider's
 * effect (audit runtime-03): React Compiler 1.0 bails out of a whole component
 * on a value block inside try/finally (facebook/react#34131), so the tick's
 * try/catch/finally kept HealthProvider from compiling. As a plain function
 * called from the effect it is outside the compiler's scope, and the provider
 * compiles. Behaviour is unchanged: the debounce, fast-poll, resume, git_sha,
 * connection and recovery rules are HealthProvider's (see its header). The
 * pure helpers the loop calls moved with it, so this module never imports
 * HealthProvider back; HealthProvider re-exports them.
 */

/** A request that could not reach the server triggers a probe, at most this often. */
const NUDGE_MIN_GAP_MS = 1_000;

/**
 * Should the poll run at the fast cadence? Only when there is something
 * TRANSIENT to watch recover — a dependency reported down, or a breaker
 * open. A backend-declared `status: 'degraded'` with every dependency up and
 * every breaker closed is a deployment-shaped condition (e.g. the treatment
 * runtime not enabled on a baseline deploy), which does not clear on a
 * three-second timescale; polling it four times per page view is pure
 * round-trip cost. Deliberately NOT `computeDegraded`: the pill and the
 * banner keep reading that, so what the UI SAYS about health is unchanged —
 * only how often it asks (2026-08-07 audit M4). A dependency `resuming`
 * (delivery-01) is transient too: the fast cadence ends the calm pill within
 * one short poll of the resume finishing.
 */
export function shouldPollFast(health: HealthPayload | null): boolean {
  if (!health) return false;
  const deps = health.dependencies ?? {};
  const transient = (state: string | undefined) => state === 'down' || state === 'resuming';
  if (transient(deps.warehouse) || transient(deps.lakebase) || transient(deps.genie)) return true;
  return Object.values(health.circuit_breakers ?? {}).some((state) => state === 'open');
}

/** Per-dependency state we track for the debounce. `pendingUpSince` is
 *  the wall-clock ms (`performance.now()`) at which we first saw an
 *  "up" probe after a "down" — null when the dep is currently "up" or
 *  has never been "up". `filtered` is what we expose to consumers. */
export interface DebounceState {
  filtered: 'up' | 'down' | 'resuming' | 'unknown';
  pendingUpSince: number | null;
}

const DEPS_TRACKED = ['warehouse', 'lakebase', 'genie'] as const;

/** Apply the down→up debounce to a fresh raw payload + the prior
 *  per-dep filter state. Pure function so it is testable in isolation
 *  and the cadence loop remains readable.
 *
 *  Rules per dep:
 *    raw=down  → filtered=down, clear pending          (instant outage)
 *    raw=up + filtered already up → keep up
 *    raw=up + filtered=down + no pending → start pending (filtered stays down)
 *    raw=up + filtered=down + pending elapsed ≥ debounceMs → flip to up, clear pending
 *    raw=up + filtered=down + pending not elapsed → stay down (still debouncing)
 *    raw=resuming → filtered=resuming at once, clear pending (not an outage)
 *    raw=up + filtered=resuming → flip to up at once (a finished resume is
 *                                 not a flap; the debounce is for down → up)
 *    raw=unknown → keep prior filter (don't flap on missing data)
 *
 *  Returned payload is a shallow copy of `raw` with `dependencies`
 *  replaced by the filtered dict. Consumers (DegradedBanner, Topbar
 *  pill) keep reading the same `HealthPayload` shape — no API change.
 */
export function applyDownUpDebounce(
  raw: HealthPayload,
  prior: Record<string, DebounceState>,
  nowMs: number,
  debounceMs: number,
): { payload: HealthPayload; next: Record<string, DebounceState> } {
  const rawDeps = raw.dependencies ?? {};
  const next: Record<string, DebounceState> = { ...prior };
  const filteredDeps: Record<string, string> = { ...rawDeps };
  for (const dep of DEPS_TRACKED) {
    const rawState = rawDeps[dep];
    const priorState = prior[dep] ?? { filtered: 'unknown', pendingUpSince: null };
    if (rawState === 'down') {
      next[dep] = { filtered: 'down', pendingUpSince: null };
      filteredDeps[dep] = 'down';
    } else if (rawState === 'resuming') {
      next[dep] = { filtered: 'resuming', pendingUpSince: null };
      filteredDeps[dep] = 'resuming';
    } else if (rawState === 'up') {
      if (priorState.filtered === 'up' || priorState.filtered === 'resuming') {
        next[dep] = { filtered: 'up', pendingUpSince: null };
        filteredDeps[dep] = 'up';
      } else if (debounceMs <= 0) {
        next[dep] = { filtered: 'up', pendingUpSince: null };
        filteredDeps[dep] = 'up';
      } else if (priorState.filtered === 'unknown') {
        // First-ever observation for this dep → trust it (there's no
        // prior down to debounce against). pendingUpSince stays null.
        next[dep] = { filtered: 'up', pendingUpSince: null };
        filteredDeps[dep] = 'up';
      } else if (priorState.pendingUpSince === null) {
        // Prior was "down", now first "up" probe → start the debounce
        // window. Keep showing "down" until the window elapses.
        next[dep] = { filtered: 'down', pendingUpSince: nowMs };
        filteredDeps[dep] = 'down';
      } else if (nowMs - priorState.pendingUpSince >= debounceMs) {
        next[dep] = { filtered: 'up', pendingUpSince: null };
        filteredDeps[dep] = 'up';
      } else {
        next[dep] = priorState; // still debouncing
        filteredDeps[dep] = 'down';
      }
    } else {
      // unknown / missing — preserve prior filter; don't claim transitions.
      next[dep] = priorState;
      if (priorState.filtered !== 'unknown') {
        filteredDeps[dep] = priorState.filtered;
      }
    }
  }
  return {
    payload: { ...raw, dependencies: filteredDeps },
    next,
  };
}

/** Everything the loop reads or writes: HealthProvider's props, refs and setters. */
export interface HealthPollOptions {
  fetchHealth: (signal?: AbortSignal, hint?: HealthHint) => Promise<HealthPayload>;
  pollIntervalOkMs: number;
  pollIntervalDegradedMs: number;
  debounceUpMs: number;
  queryClient: QueryClient | undefined;
  loadedGitShaRef: RefObject<string | null>;
  breakersRef: RefObject<HealthPayload['circuit_breakers']>;
  pollFastRef: RefObject<boolean>;
  debounceRef: RefObject<Record<string, DebounceState>>;
  connectionRef: RefObject<ConnectionTracker>;
  lastInputAtRef: RefObject<number | null>;
  setHealth: Dispatch<SetStateAction<HealthPayload | null>>;
  setProbeMs: Dispatch<SetStateAction<number | null>>;
  setFetchedAt: Dispatch<SetStateAction<string | null>>;
  setUpdateAvailable: Dispatch<SetStateAction<boolean>>;
  setConnection: Dispatch<SetStateAction<ConnectionStatus>>;
  setWarehouseResumingSince: Dispatch<SetStateAction<number | null>>;
}

/** Starts the poll (the first probe runs at once) and returns its cleanup. */
export function startHealthPoll({
  fetchHealth,
  pollIntervalOkMs,
  pollIntervalDegradedMs,
  debounceUpMs,
  queryClient,
  loadedGitShaRef,
  breakersRef,
  pollFastRef,
  debounceRef,
  connectionRef,
  lastInputAtRef,
  setHealth,
  setProbeMs,
  setFetchedAt,
  setUpdateAvailable,
  setConnection,
  setWarehouseResumingSince,
}: HealthPollOptions): () => void {
  const ctrl = new AbortController();
  let timer: ReturnType<typeof setTimeout> | null = null;
  let cancelled = false;
  let inFlight = false;
  let lastProbeEndedAt = Number.NEGATIVE_INFINITY;

  const isHidden = () =>
    typeof document !== 'undefined' && document.visibilityState === 'hidden';
  // Nothing to learn from a probe once the session ended (only a reload
  // recovers) or while the browser has no network.
  const mayProbe = () => !cancelled && !isHidden() && !isSessionExpired() && onlineManager.isOnline();

  const observeConnection = (probeReachable: boolean | null) => {
    const prior = connectionRef.current;
    const next = nextConnection(prior, {
      sessionExpired: isSessionExpired(),
      online: onlineManager.isOnline(),
      probeReachable,
    });
    connectionRef.current = next;
    setConnection(next.status);
    // A reachable probe after one or more failed ones: reload the reads
    // that could not reach the server in between, whether the banner showed
    // (two failures) or it was a one-probe blip.
    if (prior.failedProbes > 0 && next.status === 'online' && probeReachable && queryClient) {
      refetchUnreachableQueries(queryClient);
    }
  };

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  // Whole seconds since the last input: the only thing the probe tells the
  // server about the tab (the `activity` keep-warm policy reads it).
  const idleHint = (): HealthHint => {
    const last = lastInputAtRef.current;
    return { idleS: last === null ? 0 : Math.max(0, Math.floor((Date.now() - last) / 1000)) };
  };

  // The warehouse entering `resuming` stamps the pill's timer; leaving it clears it.
  const trackResuming = (prior: Record<string, DebounceState>, next: Record<string, DebounceState>) => {
    const is = next.warehouse?.filtered === 'resuming';
    if (is !== (prior.warehouse?.filtered === 'resuming')) setWarehouseResumingSince(is ? Date.now() : null);
  };

  const scheduleNext = () => {
    clearTimer();
    if (!mayProbe()) return;
    const fast = pollFastRef.current || connectionRef.current.failedProbes > 0;
    const delay = fast ? pollIntervalDegradedMs : pollIntervalOkMs;
    timer = setTimeout(() => {
      void tick();
    }, delay);
  };

  const tick = async () => {
    if (cancelled || isHidden() || inFlight) return;
    if (!mayProbe()) {
      clearTimer();
      observeConnection(null);
      return;
    }
    inFlight = true;
    const t0 = performance.now();
    try {
      const rawPayload = await fetchHealth(ctrl.signal, idleHint());
      if (cancelled) return;
      observeConnection(rawPayload.status !== 'unreachable');
      const elapsed = Math.round(performance.now() - t0);
      const { payload, next } = applyDownUpDebounce(
        rawPayload,
        debounceRef.current,
        performance.now(),
        debounceUpMs,
      );
      const recovered = recoveredDependencies(
        debounceRef.current,
        next,
        breakersRef.current,
        payload.circuit_breakers,
      );
      trackResuming(debounceRef.current, next);
      debounceRef.current = next;
      breakersRef.current = payload.circuit_breakers;
      const gitSha = (rawPayload.git_sha ?? '').trim();
      if (gitSha) {
        if (loadedGitShaRef.current === null) loadedGitShaRef.current = gitSha;
        else if (gitSha !== loadedGitShaRef.current) setUpdateAvailable(true);
      }
      setHealth(payload);
      setProbeMs(elapsed);
      setFetchedAt(new Date().toISOString());
      pollFastRef.current = shouldPollFast(payload);
      if (queryClient) refetchRecoveredQueries(queryClient, recovered);
    } catch (err) {
      if (isAbortError(err) || cancelled) return;
      // api.health() swallows network errors internally and returns
      // an "unreachable" payload, so landing here means the caller
      // passed a custom fetcher that actually threw. Treat that as
      // degraded so the next tick runs faster — and feed it through
      // the same debounce so a single thrown probe doesn't immediately
      // wipe out a healthy filter.
      const fakeRaw: HealthPayload = {
        status: 'degraded',
        mode: 'unknown',
        dependencies: { warehouse: 'down', lakebase: 'down' },
      };
      const { payload, next } = applyDownUpDebounce(
        fakeRaw,
        debounceRef.current,
        performance.now(),
        debounceUpMs,
      );
      trackResuming(debounceRef.current, next);
      debounceRef.current = next;
      setHealth(payload);
      setProbeMs(null);
      setFetchedAt(new Date().toISOString());
      pollFastRef.current = true;
    } finally {
      inFlight = false;
      lastProbeEndedAt = performance.now();
    }
    scheduleNext();
  };

  void tick();
  const onVisibilityChange = () => {
    if (isHidden()) {
      clearTimer();
    } else {
      void tick();
    }
  };
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange);
  }
  // Offline -> online clears the offline state and probes at once;
  // online -> offline stops the loop until the network returns.
  const unsubscribeOnline = onlineManager.subscribe((online) => {
    observeConnection(null);
    if (online) void tick();
    else clearTimer();
  });
  const unsubscribeSession = subscribeSessionStatus(() => {
    observeConnection(null);
    if (isSessionExpired()) clearTimer();
  });
  const unsubscribeFailures = subscribeNetworkFailures(() => {
    if (inFlight || performance.now() - lastProbeEndedAt < NUDGE_MIN_GAP_MS) return;
    void tick();
  });
  return () => {
    cancelled = true;
    ctrl.abort();
    clearTimer();
    unsubscribeOnline();
    unsubscribeSession();
    unsubscribeFailures();
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibilityChange);
    }
  };
}
