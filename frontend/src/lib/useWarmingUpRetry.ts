import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ApiError,
  dependencyLabel,
  isAbortError,
  isWarmingUpError,
} from './api';

/**
 * useWarmingUpRetry — shared cold-start retry loop for any API call that
 * reads live Unity Catalog / Lakebase data.
 *
 * Cold-boot symptom (2026-04-23): Databricks SQL warehouses auto-suspend
 * when idle and take ~30–60s to warm. During that window the backend's
 * `_dependency_down_handler` returns HTTP 503 with
 * `{retryable: true, dependency: "warehouse"}`. `api.ts`'s
 * `_fetchWithRetry` already retries 3× with backoff, but on a genuinely
 * cold warehouse that still isn't enough — the user sees a red "Backend
 * unavailable" banner on first nav.
 *
 * This hook adds a second, user-visible retry layer:
 *   - runs `fetcher(signal)` on mount and when `deps` change.
 *   - on an `ApiError` where `isWarmingUpError(err)` is true: swap the
 *     red banner for a "warming up — attempt N of 6" status block and
 *     auto-retry every 5s up to 6 attempts (30s total wall-clock).
 *   - on any other error: surface it as `error` and stop retrying.
 *   - on success at any attempt: clear any warming-up state.
 *
 * The caller owns how to render the three states (`data`, `warmingUp`,
 * `error`). `manualRetry()` is exposed so a visible "Retry" button can
 * kick the fetch back to attempt 1 after exhaustion.
 *
 * `AbortController` is wired so the in-flight fetch is cancelled on
 * unmount or when `deps` change (prevents setState-after-unmount and
 * prevents a stale response from clobbering a newer one).
 */

export interface WarmingUpState {
  /** Which dependency returned 503 — "warehouse", "lakebase", "genie", … */
  dependency: string | null;
  /** Human-facing eyebrow label, e.g. "Warehouse warming up". */
  label: string;
  /** 1-indexed attempt counter (1 … maxAttempts). */
  attempt: number;
  /** Upper bound — 6 unless caller overrides. */
  maxAttempts: number;
  /** Correlation id from the 503 body, if any — for support tickets. */
  correlationId: string | null;
}

export interface UseWarmingUpRetryResult<T> {
  data: T | null;
  /** Non-null while in a 503 warming-up loop. */
  warmingUp: WarmingUpState | null;
  /** Non-null after warming-up attempts are exhausted OR on a genuine error. */
  error: ApiError | Error | null;
  /** Force a retry — resets attempt counter. */
  manualRetry: () => void;
}

export interface UseWarmingUpRetryOpts {
  /** Default 6. */
  maxAttempts?: number;
  /** Interval between warming-up retries in ms. Default 5000. */
  intervalMs?: number;
  /** If false, the hook is a no-op (used when an id param is missing). */
  enabled?: boolean;
}

export function useWarmingUpRetry<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
  opts: UseWarmingUpRetryOpts = {},
): UseWarmingUpRetryResult<T> {
  const maxAttempts = opts.maxAttempts ?? 6;
  const intervalMs = opts.intervalMs ?? 5000;
  const enabled = opts.enabled ?? true;

  const [data, setData] = useState<T | null>(null);
  const [warmingUp, setWarmingUp] = useState<WarmingUpState | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  // Keep the latest fetcher in a ref so the effect doesn't re-run on
  // every parent render. Callers typically pass an inline closure.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const manualRetry = useCallback(() => {
    setReloadToken((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!enabled) {
      setData(null);
      setWarmingUp(null);
      setError(null);
      return;
    }
    const ctrl = new AbortController();
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    const clearTimer = () => {
      if (timeoutId !== null) {
        clearTimeout(timeoutId);
        timeoutId = null;
      }
    };

    const runAttempt = async (attempt: number): Promise<void> => {
      if (cancelled) return;
      try {
        const result = await fetcherRef.current(ctrl.signal);
        if (cancelled) return;
        setData(result);
        setWarmingUp(null);
        setError(null);
      } catch (err: unknown) {
        if (cancelled || isAbortError(err)) return;
        if (isWarmingUpError(err) && attempt < maxAttempts) {
          // Transient cold-start: show warming-up block, schedule next.
          setWarmingUp({
            dependency: err.dependency,
            label: `${dependencyLabel(err.dependency)} warming up`,
            attempt: attempt + 1,
            maxAttempts,
            correlationId: err.correlationId,
          });
          setError(null);
          timeoutId = setTimeout(() => {
            void runAttempt(attempt + 1);
          }, intervalMs);
          return;
        }
        // Either non-warming-up error or we exhausted attempts.
        setWarmingUp(null);
        setError(err instanceof Error ? err : new Error(String(err)));
      }
    };

    // Reset state on re-run so callers don't see stale data while the
    // next fetch is in flight (important when `deps` change — e.g.
    // borrower id switch).
    setData(null);
    setWarmingUp(null);
    setError(null);

    void runAttempt(1);

    return () => {
      cancelled = true;
      clearTimer();
      ctrl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, reloadToken, ...deps]);

  return { data, warmingUp, error, manualRetry };
}
