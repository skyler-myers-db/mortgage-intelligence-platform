import { useEffect, useSyncExternalStore } from 'react';
import { useGenieTurn, type GenieTurnSurface } from '../../lib/genieInFlightTurn';
import { genieProgressLabel } from './GenieProgress';

/**
 * One screen-reader announcer per Genie surface, and never two speaking at
 * once (audit 2026-09-21 `a11y-06`).
 *
 * Each surface renders ONE persistent sr-only polite region and fills it with
 * the text this hook returns. While a turn is in flight the text is the stage
 * label (`genieProgressLabel`), so it changes only when the STAGE changes --
 * never with the elapsed clock, the trace or the SQL preview. Otherwise it is
 * the turn store's announcement ("Answer ready", a refusal line, Stop, a copy
 * result, an action result).
 *
 * The floating panel and `/ask-genie` can both be mounted, and both read the
 * same turn, so a module registry picks exactly one speaker: the panel when
 * it is open or no route is mounted, otherwise the route. The silent surface
 * returns '' and stays constant.
 */

interface SurfaceRegistry {
  panel: number;
  route: number;
  panelOpen: boolean;
  routeAskVisible: boolean;
}

let registry: SurfaceRegistry = { panel: 0, route: 0, panelOpen: false, routeAskVisible: false };
let speaker: GenieTurnSurface | null = null;
const listeners = new Set<() => void>();

function speakerOf(current: SurfaceRegistry): GenieTurnSurface | null {
  if (current.panel > 0 && (current.panelOpen || current.route === 0)) return 'panel';
  if (current.route > 0) return 'route';
  return null;
}

function setRegistry(next: SurfaceRegistry): void {
  registry = next;
  const nextSpeaker = speakerOf(next);
  if (nextSpeaker === speaker) return;
  speaker = nextSpeaker;
  for (const listener of listeners) listener();
}

function registerSurface(surface: GenieTurnSurface): () => void {
  setRegistry({ ...registry, [surface]: registry[surface] + 1 });
  return () => {
    const count = Math.max(0, registry[surface] - 1);
    const visibility = surface === 'panel' ? { panelOpen: false } : { routeAskVisible: false };
    setRegistry({ ...registry, [surface]: count, ...(count === 0 ? visibility : {}) });
  };
}

function setSurfaceVisible(surface: GenieTurnSurface, visible: boolean): void {
  setRegistry(surface === 'panel' ? { ...registry, panelOpen: visible } : { ...registry, routeAskVisible: visible });
}

function subscribeSpeaker(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSpeaker(): GenieTurnSurface | null {
  return speaker;
}

function getServerSpeaker(): GenieTurnSurface | null {
  return null;
}

/** True while `/ask-genie` shows its Ask tab: a turn landing there is seen,
 *  so the launcher does not badge it. */
export function isGenieRouteAskVisible(): boolean {
  return registry.route > 0 && registry.routeAskVisible;
}

/**
 * The announcer text for `surface`. `visible` is the panel's open state, or
 * whether the route's Ask tab is shown.
 */
export function useGenieAnnouncer(surface: GenieTurnSurface, visible: boolean): string {
  useEffect(() => registerSurface(surface), [surface]);
  useEffect(() => {
    setSurfaceVisible(surface, visible);
  }, [surface, visible]);
  const current = useSyncExternalStore(subscribeSpeaker, getSpeaker, getServerSpeaker);
  const { inFlight, announcement } = useGenieTurn();
  if (current !== surface) return '';
  return inFlight ? genieProgressLabel(inFlight.progress) : announcement;
}

export function __resetGenieAnnouncerForTests(): void {
  registry = { panel: 0, route: 0, panelOpen: false, routeAskVisible: false };
  speaker = null;
  for (const listener of listeners) listener();
}
