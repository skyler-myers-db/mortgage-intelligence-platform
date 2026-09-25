import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
} from 'react';
import { QueryClientContext } from '@tanstack/react-query';
import { api, type HealthPayload } from '../lib/api';
import type { HealthHint } from '../lib/apiTypes';
import type { ActorIdentity } from '../lib/healthTrust';
import { normalizeWorkspaceHost } from '../lib/ucAssetLinks';
import { INITIAL_CONNECTION, type ConnectionStatus } from './connectionState';
import { startHealthPoll, type DebounceState } from './healthPoll';

export { applyDownUpDebounce, shouldPollFast } from './healthPoll';

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
  /**
   * The actor the last TRUSTED probe observed (lib/healthTrust): null until
   * the first one. An unreachable or thrown probe never changes it; the
   * object is replaced only when the key changes. The shell's actor boundary
   * keys on this, never on `health.actor_cache_key`.
   */
  actorIdentity: ActorIdentity | null;
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
  const [actorIdentity, setActorIdentity] = useState<ActorIdentity | null>(null);
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

  useEffect(
    () =>
      startHealthPoll({
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
        setActorIdentity,
      }),
    [fetchHealth, pollIntervalDegradedMs, pollIntervalOkMs, debounceUpMs, queryClient],
  );

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
      actorIdentity,
    }),
    [health, probeMs, fetchedAt, updateAvailable, connection, warehouseResumingSince, actorIdentity],
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
