/**
 * useMapModeParams — the geography map's colouring and Rate Lever step in
 * the URL (D-dataviz-geo-d2 2(iii), audit wow-stage-1;
 * deviation:map-mode-url). Home and Segment Intelligence own it and pass
 * `mode`, `step`, `onModeChange` and `onStepCommit` to `<USChoroplethMap>`,
 * so a shared link opens the same colouring at the same scenario step, and
 * Back / Forward restore it. The prototype's map
 * (design_files/Module 0 Prototype.html ~L1802-1812) keeps no view state in
 * the URL; the drill already lives there (useMapSelectionParams).
 *
 *  - `map_mode` is `rate` or `unattended`; absent (or anything else) means
 *    borrowers.
 *  - `rate_step` is an integer in basis points, honoured only with
 *    `map_mode=rate`; a non-integer reads as 0.
 *
 * Writes REPLACE the history entry (a scrub is not a navigation) and keep
 * every other param; `rate_step` is dropped at 0 or outside rate mode and
 * `map_mode` for borrowers. The URL is only a request: the map still falls
 * back to borrowers under a cohort filter (whole-book grid) and reads the
 * grid only when rate mode is effective; nothing is prefetched.
 */
import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import type { MapColorMode } from './USChoroplethMapHeader';

export const MAP_MODE_PARAM = 'map_mode';
export const RATE_STEP_PARAM = 'rate_step';

export function parseMapMode(raw: string | null): MapColorMode {
  return raw === 'rate' || raw === 'unattended' ? raw : 'borrowers';
}

/** The step a link asks for: an integer bps in rate mode, else 0. */
export function parseRateStep(raw: string | null, mode: MapColorMode): number {
  if (mode !== 'rate' || raw === null || !/^-?\d+$/.test(raw.trim())) return 0;
  const step = Number(raw.trim());
  return Number.isSafeInteger(step) ? step : 0;
}

/** A copy of `params` carrying the colouring and step; every other param is kept. */
export function withMapMode(params: URLSearchParams, mode: MapColorMode, step: number): URLSearchParams {
  const next = new URLSearchParams(params);
  if (mode === 'borrowers') next.delete(MAP_MODE_PARAM);
  else next.set(MAP_MODE_PARAM, mode);
  if (mode === 'rate' && Number.isSafeInteger(step) && step !== 0) next.set(RATE_STEP_PARAM, String(step));
  else next.delete(RATE_STEP_PARAM);
  return next;
}

/**
 * The grid step closest to `value`, ties going to the lower step: the same
 * rule as rateScenario.recount's nearestStep, which ships in the lazy control
 * chunk and must stay out of the map chunk.
 */
export function snapStep(steps: readonly number[], value: number): number {
  let best = steps[0] ?? 0;
  for (const step of steps) {
    if (Math.abs(step - value) < Math.abs(best - value)) best = step;
  }
  return best;
}

export interface MapModeParams {
  mode: MapColorMode;
  step: number;
  setMode: (mode: MapColorMode) => void;
  /** A committed step (pointerup, a key, Reset): replaces the URL's step in rate mode. */
  setStep: (step: number) => void;
}

export function useMapModeParams(): MapModeParams {
  const [searchParams, setSearchParams] = useSearchParams();
  const mode = parseMapMode(searchParams.get(MAP_MODE_PARAM));
  const step = parseRateStep(searchParams.get(RATE_STEP_PARAM), mode);
  const setMode = useCallback(
    (next: MapColorMode) => {
      setSearchParams((current) => {
        const currentMode = parseMapMode(current.get(MAP_MODE_PARAM));
        const keep = parseRateStep(current.get(RATE_STEP_PARAM), currentMode);
        return withMapMode(current, next, next === 'rate' ? keep : 0);
      }, { replace: true });
    },
    [setSearchParams],
  );
  const setStep = useCallback(
    (next: number) => {
      setSearchParams((current) => withMapMode(current, parseMapMode(current.get(MAP_MODE_PARAM)), next), { replace: true });
    },
    [setSearchParams],
  );
  return useMemo(() => ({ mode, step, setMode, setStep }), [mode, step, setMode, setStep]);
}

export interface MapColoringInput {
  /** The colouring the route asks for; undefined leaves the map uncontrolled. */
  mode?: MapColorMode;
  /** The step the route asks for, in bps. */
  step?: number;
  onModeChange?: (mode: MapColorMode) => void;
}

/**
 * The map's colouring and Rate Lever step: controlled by the route's
 * `mode` / `onModeChange` when given (else the map keeps its own). The step
 * the thumb and the deferred fill follow stays map state, so a scrub never
 * waits on the URL, and it re-syncs from the `step` prop when that changes
 * (Back / Forward, a shared link).
 */
export function useMapColoring({ mode: modeProp, step: stepProp, onModeChange }: MapColoringInput) {
  const [ownMode, setOwnMode] = useState<MapColorMode>('borrowers');
  const setMode = useCallback(
    (next: MapColorMode) => {
      if (modeProp === undefined) setOwnMode(next);
      onModeChange?.(next);
    },
    [modeProp, onModeChange],
  );
  const [rateStep, setRateStep] = useState(stepProp ?? 0);
  const [syncedStep, setSyncedStep] = useState(stepProp);
  if (stepProp !== syncedStep) {
    setSyncedStep(stepProp);
    if (stepProp !== undefined) setRateStep(stepProp);
  }
  return { mode: modeProp ?? ownMode, setMode, rateStep, setRateStep };
}
