/**
 * useChoroplethLiveFacts — every network-backed fact the geography map
 * renders: the lazily-imported state topology, the per-state rollups (which
 * re-fetch on segment filter / mode / portfolio criteria), the per-state ZIP
 * rollups fetched on drill, and the S9 assigned-vs-unattended overlay.
 * Extracted from USChoroplethMap.tsx (file-size gate, plan item 3); the drill
 * state itself (level / selected / hover / drillStateId) stays in the
 * component, so the hover reset those effects perform arrives here as the
 * component's own `setHover`.
 */

import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import { api, ApiError } from '../../lib/api';
import type { GeoAssignmentOverlayResponse, GeoOverlayLevel } from '../../lib/api';
import type { StateRollup, ZipRollup } from '../../types';
import type { HoverState, Level, UsaSvgMap } from './USChoroplethMap.utils';
import { loadUsaStateMap } from './USStateMapData';

export interface UseChoroplethLiveFactsInput {
  /** Current drill level — the ZIP and overlay fetches key off it. */
  level: Level;
  /** Lowercase state id the user drilled into, or null at US level. */
  drillStateId: string | null;
  segmentFilter?: string[];
  segmentFilterMode: 'any' | 'all';
  portfolioCriteria?: Record<string, string | number | null | undefined>;
  /** The component's own hover setter. Stable across renders (useState), so
   *  listing it in the dep arrays below never re-runs a fetch. */
  setHover: Dispatch<SetStateAction<HoverState | null>>;
}

export function useChoroplethLiveFacts({
  level,
  drillStateId,
  segmentFilter,
  segmentFilterMode,
  portfolioCriteria,
  setHover,
}: UseChoroplethLiveFactsInput) {
  const [usaMap, setUsaMap] = useState<UsaSvgMap | null>(null);
  // Per-state rollups from /api/geo/state-rollups. `null` = loading; `{}`
  // = API unreachable. We keep the static geography interactive, but do
  // not surface static borrower counts while live rollups are loading.
  // Keyed by lowercase state code to match state map location ids.
  const [liveStateFacts, setLiveStateFacts] = useState<Record<string, StateRollup> | null>(null);
  // Per-state ZIP rollups lazy-loaded on drill. Keyed by UPPERCASE state
  // code (the API key); each value is a dict keyed by 5-digit ZIP so the
  // tile renderer can O(1) look up a ZIP's live count / avg score.
  const [liveZipFacts, setLiveZipFacts] = useState<Record<string, Record<string, ZipRollup>>>({});
  // S9 assigned-vs-unattended overlay. `overlayOn` toggles the recolor +
  // tooltip extension. `overlayData` is the response for the CURRENT drill
  // level; `null` = not yet loaded, `overlayError` = fetch failed (degraded
  // note in the legend, base borrower view stays functional).
  const [overlayOn, setOverlayOn] = useState(false);
  const [overlayData, setOverlayData] = useState<GeoAssignmentOverlayResponse | null>(null);
  const [overlayError, setOverlayError] = useState<string | null>(null);
  const [overlayLoading, setOverlayLoading] = useState(false);

  const segmentFilterKey = useMemo(
    () => (segmentFilter && segmentFilter.length > 0 ? segmentFilter.join(',') : ''),
    [segmentFilter],
  );
  const portfolioCriteriaKey = useMemo(
    () => JSON.stringify(portfolioCriteria ?? {}),
    [portfolioCriteria],
  );

  useEffect(() => {
    setHover(null);
    setLiveZipFacts({});
  }, [segmentFilterKey, segmentFilterMode, portfolioCriteriaKey, setHover]);

  // Lazy-load the state geography so the TopoJSON conversion lands in its
  // own code-split chunk instead of the main bundle.
  useEffect(() => {
    let cancelled = false;
    loadUsaStateMap().then((map) => {
      if (!cancelled) setUsaMap(map);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch per-state rollups from the backend. Counts, score, tint, and
  // top-segment labels all come from the live response; on error the map
  // stays interactive but metric fields render as unknown.
  //
  // 2026-05-04 (FIX G): the effect now re-runs whenever segmentFilter
  // changes so the per-state counts (and the choropleth bucketer
  // derived from them) reflect the active segment selection. Without
  // a filter we use the cross-segment _ALL row; with a filter we hit
  // the segment-aware path. `segmentFilterMode="any"` counts a
  // de-duplicated OR cohort; `segmentFilterMode="all"` counts borrowers
  // that match every selected segment.
  useEffect(() => {
    let cancelled = false;
    setHover(null);
    setLiveStateFacts(null);
    api
      .stateRollups(
        segmentFilter && segmentFilter.length > 0 ? segmentFilter : null,
        undefined,
        segmentFilterMode,
        portfolioCriteria,
      )
      .then((payload) => {
        if (cancelled) return;
        const byCode: Record<string, StateRollup> = {};
        for (const r of payload.rollups) {
          byCode[r.state.toLowerCase()] = r;
        }
        setLiveStateFacts(byCode);
      })
      .catch(() => {
        // Keep the geography interactive, but do not surface static
        // borrower counts as if they were live. The tooltip renders
        // "—" until the rollup endpoint recovers.
        if (!cancelled) setLiveStateFacts({});
      });
    return () => {
      cancelled = true;
    };
  }, [segmentFilter, segmentFilterMode, portfolioCriteria, portfolioCriteriaKey, setHover]);

  // Lazy-fetch ZIP rollups when the user drills into a state.
  // /api/geo/zip-rollups?state=XX reads mip.gold.zip_rollup on its real
  // grain. A state with no ZIP rows resolves to an empty list; the ZIP
  // renderer shows an honest empty state with a Lead Queue fallback.
  useEffect(() => {
    if (level !== 'zip' || !drillStateId) return;
    const stateUC = drillStateId.toUpperCase();
    if (liveZipFacts[stateUC]) return;
    let cancelled = false;
    api
      .zipRollups(
        { state: stateUC },
        undefined,
        segmentFilter && segmentFilter.length > 0 ? segmentFilter : null,
        segmentFilterMode,
        portfolioCriteria,
      )
      .then((payload) => {
        if (cancelled) return;
        const byZip: Record<string, ZipRollup> = {};
        for (const r of payload.rollups) byZip[r.zip] = r;
        setLiveZipFacts((cur) => ({ ...cur, [stateUC]: byZip }));
      })
      .catch(() => {
        // Empty dict means "we tried, nothing came back" -- the renderer
        // shows the empty state instead of re-fetching on every render.
        if (!cancelled) setLiveZipFacts((cur) => ({ ...cur, [stateUC]: {} }));
      });
    return () => {
      cancelled = true;
    };
  }, [level, drillStateId, liveZipFacts, segmentFilter, segmentFilterMode, portfolioCriteria, portfolioCriteriaKey]);

  // S9 overlay fetch. Re-runs on drill level + toggle. Each level maps to a
  // distinct /api/geo/assignment-overlay call (state | zip+state). A fetch
  // failure sets an honest degraded note and leaves the base borrower view
  // untouched -- never a silent fallback.
  useEffect(() => {
    if (!overlayOn) {
      setOverlayData(null);
      setOverlayError(null);
      setOverlayLoading(false);
      return;
    }
    // Determine the request for the current drill level. Skip until the
    // parent state needed for a ZIP request is known. The ZIP overlay keys
    // on the same state as the tiles it recolors.
    let request: { level: GeoOverlayLevel; state?: string | null; countyFips?: string | null } | null = null;
    if (level === 'state') {
      request = { level: 'state' };
    } else if (level === 'zip' && drillStateId) {
      request = { level: 'zip', state: drillStateId.toUpperCase() };
    }
    if (!request) {
      setOverlayData(null);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setOverlayLoading(true);
    setOverlayError(null);
    setOverlayData(null);
    api
      .assignmentOverlay(request.level, {
        state: request.state,
        countyFips: request.countyFips,
        signal: controller.signal,
      })
      .then((payload) => {
        if (cancelled) return;
        setOverlayData(payload);
        setOverlayLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled || (err instanceof ApiError && err.aborted)) return;
        const dep = err instanceof ApiError && err.dependency ? ` (${err.dependency})` : '';
        setOverlayError(`Coverage overlay unavailable${dep}. Showing borrower counts.`);
        setOverlayData(null);
        setOverlayLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [overlayOn, level, drillStateId]);

  return {
    usaMap,
    liveStateFacts,
    liveZipFacts,
    overlayOn,
    setOverlayOn,
    overlayData,
    overlayError,
    overlayLoading,
  };
}
