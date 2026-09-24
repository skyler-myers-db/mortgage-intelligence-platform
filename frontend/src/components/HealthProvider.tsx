import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
} from 'react';
import { QueryClientContext, onlineManager } from '@tanstack/react-query';
import { api, isAbortError, type HealthPayload } from '../lib/api';
import type { HealthHint } from '../lib/apiTypes';
import { subscribeNetworkFailures } from '../lib/apiFailure';
import { isSessionExpired, subscribeSessionStatus } from '../lib/sessionStatus';
import { normalizeWorkspaceHost } from '../lib/ucAssetLinks';
import { recoveredDependencies, refetchRecoveredQueries } from './healthRecovery';
import {
  INITIAL_CONNECTION,
  nextConnection,
  refetchUnreachableQueries,
  type ConnectionStatus,
} from './connectionState';

/**
 * HealthProvider — one `/api/health` poll, shared via context.
 *
 * Before: shell consumers ran independent `setInterval` health-fetch loops,
 * multiplying `/api/health` load and risking inconsistent state on the same
 * page.
 *
 * Now: one poll per document, cadence flips from `pollIntervalOkMs`
 * (8s) to `pollIntervalDegradedMs` (3s) when any dependency is down.
 * Consumers pull the latest payload with `useHealth()`.
 *
 * Round-2 hole-finder #21, 2026-04-23.
 *
 * Audit 2026-09-21: the poll also owns two edges nothing else can see.
 *   - Recovery (`states-03`): when a dependency crosses down → up, the mounted
 *     queries that failed because of THAT dependency are refetched (see
 *     `healthRecovery.ts`), so panels recover on every route, not only Home.
 *   - New build (`bundle-01`): the health body carries `git_sha`. The first
 *     non-empty sha is remembered; a later, different non-empty sha flips
 *     `updateAvailable` so the shell can offer a reload before a stale tab
 *     asks for a chunk that no longer exists. Bare deploys report no sha and
 *     are ignored.
 *   - Connection (`states-02`, `shell-v1`): `connection` says whether the
 *     session ended, the browser is offline, or two probes in a row could not
 *     reach the app (see `connectionState.ts`). Probing stops once the session
 *     ended (only a reload recovers), waits for the network while offline,
 *     runs at the fast cadence after one unreachable probe, and runs at once
 *     when a request elsewhere could not reach the server.
 *   - Resume (`delivery-01`): a warehouse `resuming` from auto-stop is not an
 *     outage. It shows at once (no debounce), polls at the fast cadence, and
 *     flips straight to `up` when the resume finishes (a finished resume is
 *     not a flap); `warehouseResumingSince` times the calm topbar pill.
 *   - Activity (`delivery-v1`): the poll tells the server how long the tab has
 *     been idle (`idle_s`, whole seconds since the last pointer, key, wheel or
 *     touch input; mount counts as input) for the `activity` keep-warm policy.
 *     The listeners are passive and never update state.
 */

/** A request that could not reach the server triggers a probe, at most this often. */
const NUDGE_MIN_GAP_MS = 1_000;

/** User input that counts as activity for the keep-warm hint. */
const ACTIVITY_EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const;

interface HealthContextValue {
  /** Latest payload, or null before the first response resolves. */
  health: HealthPayload | null;
  /** Wall-clock ms for the most recent probe. null before first return. */
  probeMs: number | null;
  /** ISO timestamp of the most recent probe completion. */
  fetchedAt: string | null;
  /** True when any dependency is down or the backend returned status='degraded'. */
  degraded: boolean;
  /** True once a poll reports a different build than the one this tab loaded. */
  updateAvailable: boolean;
  /** Session / network reachability, independent of dependency health. */
  connection: ConnectionStatus;
  /** `Date.now()` when the warehouse entered `resuming`; null otherwise. */
  warehouseResumingSince: number | null;
}

const HealthContext = createContext<HealthContextValue | null>(null);

/**
 * Inspect a HealthPayload and decide if the UI should treat the stack
 * as degraded. Kept in one place so the poll-cadence flip and the
 * DegradedBanner's render decision can't drift apart.
 */
export function computeDegraded(health: HealthPayload | null): boolean {
  if (!health) return false;
  if (health.status === 'degraded') return true;
  const deps = health.dependencies ?? {};
  if (deps.warehouse === 'down') return true;
  if (deps.lakebase === 'down') return true;
  if (deps.genie === 'down') return true;
  const breakers = health.circuit_breakers ?? {};
  for (const state of Object.values(breakers)) {
    if (state === 'open') return true;
  }
  return false;
}

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

interface HealthProviderProps {
  pollIntervalOkMs?: number;
  pollIntervalDegradedMs?: number;
  /**
   * Debounce window applied to the down→up transition for each
   * dependency (warehouse / lakebase / genie). A dep that flaps back
   * to "up" briefly is reported as still "down" until it has been
   * "up" for at least this many ms across consecutive probes. The
   * up→down direction is NOT debounced — real outages surface
   * immediately. Default 5000ms (≈ 1–2 polls at degraded cadence)
   * smooths the serverless warehouse cold-start cycle without
   * masking sustained problems. Set to 0 in tests for instant flips.
   * 2026-05-04 follow-up to user feedback: "the reconnecting banner
   * seems to be up a *lot*."
   */
  debounceUpMs?: number;
  /**
   * Injected fetcher for tests. Defaults to `api.health()` which
   * already tolerates network failures (returns an "unreachable"
   * snapshot instead of throwing) and routes through the retry
   * protocol. The second argument is the tab's activity hint.
   */
  fetchHealth?: (signal?: AbortSignal, hint?: HealthHint) => Promise<HealthPayload>;
}

/** Per-dependency state we track for the debounce. `pendingUpSince` is
 *  the wall-clock ms (`performance.now()`) at which we first saw an
 *  "up" probe after a "down" — null when the dep is currently "up" or
 *  has never been "up". `filtered` is what we expose to consumers. */
interface DebounceState {
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

export function HealthProvider({
  pollIntervalOkMs = 8000,
  pollIntervalDegradedMs = 3000,
  debounceUpMs = 5000,
  fetchHealth = api.health,
  children,
}: PropsWithChildren<HealthProviderProps>) {
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [probeMs, setProbeMs] = useState<number | null>(null);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [connection, setConnection] = useState<ConnectionStatus>(INITIAL_CONNECTION.status);
  const [warehouseResumingSince, setWarehouseResumingSince] = useState<number | null>(null);
  // Optional on purpose: isolated mounts (unit tests, stories) have no
  // QueryClientProvider, and `useQueryClient()` would throw there.
  const queryClient = useContext(QueryClientContext);
  // Build this tab booted from: the first non-empty `git_sha` the poll saw.
  const loadedGitShaRef = useRef<string | null>(null);
  // Breaker states from the previous probe, for the open → closed edge.
  const breakersRef = useRef<HealthPayload['circuit_breakers']>(undefined);
  // Latest cadence decision in a ref so the polling loop reads the fresh
  // value without being re-registered every time the state flips.
  const pollFastRef = useRef(false);
  // Per-dependency debounce state. Lives in a ref so the tick closure
  // sees the latest pendingUpSince across re-renders without React
  // batching it out of order.
  const debounceRef = useRef<Record<string, DebounceState>>({});
  // Consecutive unreachable probes + the derived connection status.
  const connectionRef = useRef(INITIAL_CONNECTION);
  // Epoch ms of the last user input (mount counts). A ref, never state:
  // input must not re-render the shell.
  const lastInputAtRef = useRef<number | null>(null);

  // Declared before the poll so the mount stamp exists for its first probe.
  useEffect(() => {
    const noteInput = () => {
      lastInputAtRef.current = Date.now();
    };
    noteInput();
    const options = { capture: true, passive: true } as const;
    for (const type of ACTIVITY_EVENTS) window.addEventListener(type, noteInput, options);
    return () => {
      for (const type of ACTIVITY_EVENTS) window.removeEventListener(type, noteInput, options);
    };
  }, []);

  useEffect(() => {
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
  }, [fetchHealth, pollIntervalDegradedMs, pollIntervalOkMs, debounceUpMs, queryClient]);

  // Paused queries render their skeletons without the shimmer while the
  // browser is offline (the offline banner explains the wait); the rule keys
  // off this attribute in design-system/components/11-skeleton-*.css.
  useEffect(() => {
    if (typeof document === 'undefined') return undefined;
    const root = document.documentElement;
    if (connection === 'offline') root.dataset.connection = 'offline';
    else delete root.dataset.connection;
    return () => {
      delete root.dataset.connection;
    };
  }, [connection]);

  const value = useMemo<HealthContextValue>(
    () => ({
      health,
      probeMs,
      fetchedAt,
      degraded: computeDegraded(health),
      updateAvailable,
      connection,
      warehouseResumingSince,
    }),
    [health, probeMs, fetchedAt, updateAvailable, connection, warehouseResumingSince],
  );

  return <HealthContext.Provider value={value}>{children}</HealthContext.Provider>;
}

/**
 * Access the shared health snapshot. Returns `{ health: null, ... }`
 * before the first poll resolves so callers can render "probing"
 * states without a separate loading flag. Throws if used outside the
 * provider so misuses fail loudly instead of silently polling twice.
 */
export function useHealth(): HealthContextValue {
  const ctx = useContext(HealthContext);
  if (!ctx) throw new Error('useHealth must be used inside <HealthProvider>');
  return ctx;
}

/**
 * Optional variant that returns `null` when rendered outside a
 * HealthProvider. Used by <DegradedBanner> so it can still run as a
 * standalone component in tests (with an injected `fetchHealth`
 * prop) without requiring callers to spin up a provider tree.
 */
export function useOptionalHealth(): HealthContextValue | null {
  return useContext(HealthContext);
}

/**
 * Workspace origin published on the authenticated health body, normalized
 * and validated (see `normalizeWorkspaceHost`). Returns `null` when the poll
 * hasn't resolved, when the backend omits `workspace_host` (anonymous body /
 * older build), when the value fails validation, or when the caller renders
 * outside a HealthProvider. Callers MUST treat `null` as "render plain text",
 * never as a reason to fabricate a workspace URL.
 *
 * Reuses the shared health poll on purpose — there is exactly one
 * `/api/health` request per document and this adds no second fetch.
 */
export function useWorkspaceHost(): string | null {
  const ctx = useContext(HealthContext);
  return normalizeWorkspaceHost(ctx?.health?.workspace_host);
}
