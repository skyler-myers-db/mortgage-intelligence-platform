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
 * The tenant footprint (set of US states the lender writes business in) plus
 * discovered geography scope are the source of truth for:
 *
 *   - `USChoroplethMap.tsx` — which states drill down and how many counties
 *     the current data share actually covers.
 *   - `routes/segment-intelligence.tsx` — which state options appear in the
 *     secondary filter row.
 *   - `portfolio-builder.tsx` GEO dropdown — the "All N states" option label.
 *
 * The backend's `mip.ref.state_footprint` table plus gold geography rollups
 * drive the UI in one fetch; route defaults stay on the dynamic whole-footprint
 * option rather than a fixed state.
 *
 * Fetch posture:
 *   - One GET on provider mount, no polling. The footprint changes on the
 *     order of tenant onboarding events (months), not session minutes. If
 *     the admin edits it, they already know to reload.
 *   - On fetch failure we surface a generic US-state dictionary fallback so
 *     the map still renders and the dropdowns still have options. This is a
 *     degraded metadata state, not a claim about tenant coverage.
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
  geography_scope?: GeographyScopePayload | null;
  using_fallback?: boolean;
}

export interface GeographyScopeCounty {
  state: string;
  fips_5: string;
  county_name?: string | null;
  addressable_borrowers: number;
}

export interface GeographyScopePayload {
  state_count: number;
  county_count: number;
  zip_count?: number | null;
  snapshot_date?: string | null;
  source_table?: string | null;
  scope_label: string;
  counties: GeographyScopeCounty[];
}

export interface FootprintContextValue {
  /** Ordered list of footprint states (sorted by display_order). */
  states: FootprintState[];
  /** Just the 2-letter USPS codes, sorted. */
  stateCodes: string[];
  /** True when hydration (or fallback) has completed. */
  ready: boolean;
  /** True when the backend fetch failed and we are on the generic fallback. */
  usingFallback: boolean;
  /** Data-driven Cotality geography coverage discovered from gold rollups. */
  dataScope: GeographyScopePayload | null;
}

// Generic US-state dictionary fallback. This is stable geography metadata, not
// a tenant or Cotality-share scope. The live `/api/config/footprint` response
// replaces it on normal startup; `usingFallback` tells the shell to disclose
// when this degraded path is active.
const FALLBACK_STATES: FootprintState[] = [
  ['AL', 'Alabama'], ['AK', 'Alaska'], ['AZ', 'Arizona'], ['AR', 'Arkansas'],
  ['CA', 'California'], ['CO', 'Colorado'], ['CT', 'Connecticut'],
  ['DE', 'Delaware'], ['FL', 'Florida'], ['GA', 'Georgia'], ['HI', 'Hawaii'],
  ['ID', 'Idaho'], ['IL', 'Illinois'], ['IN', 'Indiana'], ['IA', 'Iowa'],
  ['KS', 'Kansas'], ['KY', 'Kentucky'], ['LA', 'Louisiana'], ['ME', 'Maine'],
  ['MD', 'Maryland'], ['MA', 'Massachusetts'], ['MI', 'Michigan'],
  ['MN', 'Minnesota'], ['MS', 'Mississippi'], ['MO', 'Missouri'],
  ['MT', 'Montana'], ['NE', 'Nebraska'], ['NV', 'Nevada'],
  ['NH', 'New Hampshire'], ['NJ', 'New Jersey'], ['NM', 'New Mexico'],
  ['NY', 'New York'], ['NC', 'North Carolina'], ['ND', 'North Dakota'],
  ['OH', 'Ohio'], ['OK', 'Oklahoma'], ['OR', 'Oregon'], ['PA', 'Pennsylvania'],
  ['RI', 'Rhode Island'], ['SC', 'South Carolina'], ['SD', 'South Dakota'],
  ['TN', 'Tennessee'], ['TX', 'Texas'], ['UT', 'Utah'], ['VT', 'Vermont'],
  ['VA', 'Virginia'], ['WA', 'Washington'], ['WV', 'West Virginia'],
  ['WI', 'Wisconsin'], ['WY', 'Wyoming'],
].map(([state_code, state_name], idx) => ({
  state_code,
  state_name,
  display_order: idx + 1,
  is_default_state: false,
}));

const FootprintContext = createContext<FootprintContextValue | null>(null);

interface FootprintProviderProps {
  /**
   * Injected fetcher for tests. Defaults to a native `fetch` of
   * `/api/config/footprint`. Keeping this out of `lib/api.ts` avoids
   * coupling footprint hydration to the retry/backoff loop — a
   * footprint fetch that 503s once should fall through to the generic
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
  const [ready, setReady] = useState<boolean>(false);
  const [usingFallback, setUsingFallback] = useState<boolean>(false);
  const [dataScope, setDataScope] = useState<GeographyScopePayload | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    let cancelled = false;

    // Retry once with a 1s backoff before falling back. The previous
    // single-shot fetch pinned the UI to fallback metadata whenever the
    // warehouse cold-started and the first /api/config/footprint request
    // returned 503. One retry is
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
        setDataScope(payload.geography_scope ?? null);
        setUsingFallback(Boolean(payload.using_fallback));
      } catch (err) {
        if (cancelled) return;
        if (err instanceof Error && err.name === 'AbortError') return;
        // Fall back — the map + dropdowns must render, but the Topbar
        // surfaces `usingFallback` as a muted chip so operators are not
        // silently misled about the tenant footprint.
        setStates(FALLBACK_STATES);
        setDataScope(null);
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
      ready,
      usingFallback,
      dataScope,
    }),
    [states, ready, usingFallback, dataScope],
  );

  return <FootprintContext.Provider value={value}>{children}</FootprintContext.Provider>;
}

/**
 * Access the hydrated footprint. Returns the generic US-state fallback before
 * the first fetch resolves so consumers never see an empty list
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
 * Optional variant that returns the generic fallback when rendered
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
    ready: true,
    usingFallback: true,
    dataScope: null,
  };
}
