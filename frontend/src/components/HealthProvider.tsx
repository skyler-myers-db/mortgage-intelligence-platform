import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
} from 'react';
import { api, isAbortError, type HealthPayload } from '../lib/api';

/**
 * HealthProvider — one `/api/health` poll, shared via context.
 *
 * Before: `Topbar`, `AgentActivityLog`, and `DegradedBanner` each ran
 * their own `setInterval` + `fetch('/api/health')` loop. 3× the load
 * on `/api/health`, 3 independent timers, and inconsistent state on
 * the same page (a degraded warehouse could show in the banner while
 * the topbar pill still said "up").
 *
 * Now: one poll per document, cadence flips from `pollIntervalOkMs`
 * (8s) to `pollIntervalDegradedMs` (3s) when any dependency is down.
 * Consumers pull the latest payload with `useHealth()`.
 *
 * Round-2 hole-finder #21, 2026-04-23.
 */

interface HealthContextValue {
  /** Latest payload, or null before the first response resolves. */
  health: HealthPayload | null;
  /** Wall-clock ms for the most recent probe. null before first return. */
  probeMs: number | null;
  /** ISO timestamp of the most recent probe completion. */
  fetchedAt: string | null;
  /** True when any dependency is down or the backend returned status='degraded'. */
  degraded: boolean;
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
   * Injected fetcher for tests. Defaults to `api.health()` which
   * already tolerates network failures (returns an "unreachable"
   * snapshot instead of throwing) and routes through the retry
   * protocol.
   */
  fetchHealth?: (signal?: AbortSignal) => Promise<HealthPayload>;
}

export function HealthProvider({
  pollIntervalOkMs = 8000,
  pollIntervalDegradedMs = 3000,
  fetchHealth = api.health,
  children,
}: PropsWithChildren<HealthProviderProps>) {
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [probeMs, setProbeMs] = useState<number | null>(null);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  // Latest degraded flag in a ref so the polling loop reads the fresh
  // cadence without being re-registered every time the state flips.
  const degradedRef = useRef(false);

  useEffect(() => {
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const tick = async () => {
      const t0 = performance.now();
      try {
        const payload = await fetchHealth(ctrl.signal);
        if (cancelled) return;
        const elapsed = Math.round(performance.now() - t0);
        setHealth(payload);
        setProbeMs(elapsed);
        setFetchedAt(new Date().toISOString());
        degradedRef.current = computeDegraded(payload);
      } catch (err) {
        if (isAbortError(err) || cancelled) return;
        // api.health() swallows network errors internally and returns
        // an "unreachable" payload, so landing here means the caller
        // passed a custom fetcher that actually threw. Treat that as
        // degraded so the next tick runs faster.
        setHealth({
          status: 'degraded',
          mode: 'unknown',
          dependencies: { warehouse: 'down', lakebase: 'down' },
        });
        setProbeMs(null);
        setFetchedAt(new Date().toISOString());
        degradedRef.current = true;
      }
      if (cancelled) return;
      const delay = degradedRef.current ? pollIntervalDegradedMs : pollIntervalOkMs;
      timer = setTimeout(() => {
        void tick();
      }, delay);
    };

    void tick();
    return () => {
      cancelled = true;
      ctrl.abort();
      if (timer !== null) clearTimeout(timer);
    };
  }, [fetchHealth, pollIntervalDegradedMs, pollIntervalOkMs]);

  const value = useMemo<HealthContextValue>(
    () => ({
      health,
      probeMs,
      fetchedAt,
      degraded: computeDegraded(health),
    }),
    [health, probeMs, fetchedAt],
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
