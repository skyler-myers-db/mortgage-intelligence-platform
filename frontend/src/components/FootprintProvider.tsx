import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from 'react';

/**
 * FootprintProvider — one `/api/config/footprint` hydration, shared via context.
 *
 * The tenant footprint (set of US states the lender writes business in) is the
 * single source of truth for:
 *
 *   - `SUPPORTED_COUNTY_STATES` in `USChoroplethMap.tsx` — which states drill
 *     down to county polygons.
 *   - `LOCATION_TO_STATES` in `routes/segment-intelligence.tsx` — which MSA /
 *     metro dropdown options appear in the secondary filter row.
 *   - `portfolio-builder.tsx` GEO dropdown — the "All N states" option label.
 *
 * Before this provider, each consumer hardcoded the 6-state Summit footprint
 * (IL/CA/FL/TX/WA/CO) as a literal. That was correct for the default tenant
 * but meant every new customer required a source edit. Now the backend's
 * `mip.ref.state_footprint` table (resolved via
 * `StateFootprintResolver`) drives the UI in one fetch.
 *
 * Fetch posture:
 *   - One GET on provider mount, no polling. The footprint changes on the
 *     order of tenant onboarding events (months), not session minutes. If
 *     the admin edits it, they already know to reload.
 *   - On fetch failure we surface the canonical 6-state Summit fallback so
 *     the map still renders and the dropdowns still have options — the
 *     backend's own `StateFootprintResolver` already falls back to the same
 *     list when UC is unreachable, so this keeps the two in lockstep.
 *   - `ready` flips to `true` once either the fetch resolves or the
 *     fallback is applied, so `useFootprint()` never returns a half-hydrated
 *     snapshot.
 *
 * This module contains no backend writes and no secrets — the endpoint is
 * intentionally unauthenticated (it's tenant metadata, not PII).
 */

/** One entry from `/api/config/footprint`. Mirrors the Pydantic shape. */
export interface FootprintState {
  state_code: string;        // "IL"
  state_name: string;        // "Illinois"
  display_order: number;
  is_default_state: boolean;
}

/** Response shape from `GET /api/config/footprint`. */
interface FootprintPayload {
  states: FootprintState[];
  default_state_code: string;
}

export interface FootprintContextValue {
  /** Ordered list of footprint states (sorted by display_order). */
  states: FootprintState[];
  /** Just the 2-letter USPS codes, sorted. */
  stateCodes: string[];
  /** USPS code of the `is_default_state=true` row (the "anchor" state). */
  defaultStateCode: string;
  /** True when hydration (or fallback) has completed. */
  ready: boolean;
  /** True when the backend fetch failed and we are on the static fallback. */
  usingFallback: boolean;
}

// Canonical 6-state Summit fallback — mirrors
// `sql/ref/state_footprint_seed.sql` byte-for-byte, which is also the
// backend's `_FOOTPRINT_FALLBACK` tuple in
// `backend/services/state_footprint.py`. Keep all three in lockstep.
const FALLBACK_STATES: FootprintState[] = [
  { state_code: 'IL', state_name: 'Illinois',   display_order: 1, is_default_state: true  },
  { state_code: 'CA', state_name: 'California', display_order: 2, is_default_state: false },
  { state_code: 'FL', state_name: 'Florida',    display_order: 3, is_default_state: false },
  { state_code: 'TX', state_name: 'Texas',      display_order: 4, is_default_state: false },
  { state_code: 'WA', state_name: 'Washington', display_order: 5, is_default_state: false },
  { state_code: 'CO', state_name: 'Colorado',   display_order: 6, is_default_state: false },
];

const FootprintContext = createContext<FootprintContextValue | null>(null);

interface FootprintProviderProps {
  /**
   * Injected fetcher for tests. Defaults to a native `fetch` of
   * `/api/config/footprint`. Keeping this out of `lib/api.ts` avoids
   * coupling footprint hydration to the retry/backoff loop — a
   * footprint fetch that 503s once should fall through to the static
   * fallback immediately rather than wait 3 retries.
   */
  fetchFootprint?: (signal?: AbortSignal) => Promise<FootprintPayload>;
}

async function defaultFetchFootprint(signal?: AbortSignal): Promise<FootprintPayload> {
  const res = await fetch('/api/config/footprint', { signal });
  if (!res.ok) throw new Error(`footprint fetch ${res.status}`);
  return (await res.json()) as FootprintPayload;
}

export function FootprintProvider({
  fetchFootprint = defaultFetchFootprint,
  children,
}: PropsWithChildren<FootprintProviderProps>) {
  const [states, setStates] = useState<FootprintState[]>(FALLBACK_STATES);
  const [defaultStateCode, setDefaultStateCode] = useState<string>('IL');
  const [ready, setReady] = useState<boolean>(false);
  const [usingFallback, setUsingFallback] = useState<boolean>(false);

  useEffect(() => {
    const ctrl = new AbortController();
    let cancelled = false;

    // R5-07 (2026-04-23): retry once with a 1s backoff before falling
    // back. The previous single-shot `fetch` pinned the UI to the
    // 6-state fallback whenever the warehouse cold-started and the
    // first /api/config/footprint request returned 503. One retry is
    // enough to ride out most cold-start hiccups; anything longer
    // belongs in the service-level warm-start loop, not here.
    const attempt = async (): Promise<FootprintPayload> => {
      try {
        return await fetchFootprint(ctrl.signal);
      } catch (err) {
        if (ctrl.signal.aborted) throw err;
        if (err instanceof Error && err.name === 'AbortError') throw err;
        // Wait 1s and retry once. If the controller aborts during the
        // delay, bail immediately.
        await new Promise<void>((resolve, reject) => {
          const t = setTimeout(resolve, 1000);
          ctrl.signal.addEventListener('abort', () => {
            clearTimeout(t);
            reject(new DOMException('Aborted', 'AbortError'));
          }, { once: true });
        });
        return await fetchFootprint(ctrl.signal);
      }
    };

    (async () => {
      try {
        const payload = await attempt();
        if (cancelled) return;
        if (!payload?.states || payload.states.length === 0) {
          throw new Error('empty footprint payload');
        }
        const sorted = [...payload.states].sort(
          (a, b) => a.display_order - b.display_order,
        );
        setStates(sorted);
        setDefaultStateCode(payload.default_state_code || sorted[0].state_code);
        setUsingFallback(false);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof Error && err.name === 'AbortError') return;
        // Fall back — the map + dropdowns must render. The backend's own
        // resolver falls back to the same 6-state list when UC is
        // unreachable, so the UI parity is preserved. The Topbar now
        // surfaces `usingFallback` as a muted chip so operators aren't
        // silently misled about the tenant footprint.
        setStates(FALLBACK_STATES);
        setDefaultStateCode('IL');
        setUsingFallback(true);
      } finally {
        if (!cancelled) setReady(true);
      }
    })();

    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [fetchFootprint]);

  const value = useMemo<FootprintContextValue>(
    () => ({
      states,
      stateCodes: states.map((s) => s.state_code),
      defaultStateCode,
      ready,
      usingFallback,
    }),
    [states, defaultStateCode, ready, usingFallback],
  );

  return <FootprintContext.Provider value={value}>{children}</FootprintContext.Provider>;
}

/**
 * Access the hydrated footprint. Returns the canonical 6-state fallback
 * before the first fetch resolves so consumers never see an empty list
 * (empty dropdowns would be a worse UX than a moment of "wrong" labels
 * before hydration lands).
 *
 * Throws if used outside `<FootprintProvider>` so a misuse fails loudly
 * instead of silently hardcoding the fallback forever.
 */
export function useFootprint(): FootprintContextValue {
  const ctx = useContext(FootprintContext);
  if (!ctx) throw new Error('useFootprint must be used inside <FootprintProvider>');
  return ctx;
}

/**
 * Optional variant that returns the static fallback when rendered
 * outside a `<FootprintProvider>`. Used by components that may be
 * rendered standalone in Storybook / tests (e.g. USChoroplethMap) so
 * those paths don't require spinning up a provider tree.
 */
export function useOptionalFootprint(): FootprintContextValue {
  const ctx = useContext(FootprintContext);
  if (ctx) return ctx;
  return {
    states: FALLBACK_STATES,
    stateCodes: FALLBACK_STATES.map((s) => s.state_code),
    defaultStateCode: 'IL',
    ready: true,
    usingFallback: true,
  };
}
