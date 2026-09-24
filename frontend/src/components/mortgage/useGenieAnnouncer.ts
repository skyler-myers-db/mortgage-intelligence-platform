import { useEffect, useLayoutEffect, useSyncExternalStore } from 'react';
import { getGenieTurnSnapshot, useGenieTurn, type GenieTurnSurface } from '../../lib/genieInFlightTurn';
import { genieProgressLabel } from './GenieProgress';

/**
 * One screen-reader announcer per Genie surface, and never two speaking at
 * once (audit 2026-09-21 `a11y-06`).
 *
 * Each surface renders ONE persistent sr-only polite region
 * (GenieAnnouncerRegion) and fills it with what this hook returns. While a
 * turn is in flight the text is the stage label (`genieProgressLabel`), so it
 * changes only when the STAGE changes -- never with the elapsed clock, the
 * trace or the SQL preview. Otherwise it is the turn store's announcement
 * ("Answer ready", a refusal line, Stop, a copy result, an action result).
 * An announcement made mid-turn (a copy result) is said until the stage moves
 * on, then the new stage label is.
 *
 * The floating panel and `/ask-genie` can both be mounted, and both read the
 * same turn, so a module registry picks exactly one speaker: the panel when
 * it is open or no route is mounted, otherwise the route. The silent surface
 * returns '' and stays constant.
 *
 * Each announcement is said ONCE. The store numbers them
 * (`announcementSeq`), a speaker marks the one it rendered as said, and a
 * surface that takes the floor (the panel opening or closing over
 * /ask-genie, the route mounting or unmounting, the panel remounting) starts
 * silent for everything already said. Only the in-flight stage label moves
 * with the floor: it is current state, not news. An announcement no surface
 * was mounted to say (a turn that landed while the user was elsewhere, an
 * interruption found on a reload) is said once by the next surface to take
 * the floor.
 */

/** What a surface's region renders: `key` changes whenever the text is news,
 *  so an identical announcement ("SQL copied" twice) is inserted again. */
export interface GenieAnnouncerText {
  readonly text: string;
  readonly key: string;
}

interface SurfaceRegistry {
  panel: number;
  route: number;
  panelOpen: boolean;
  routeAskVisible: boolean;
}

interface SpeakerState {
  readonly speaker: GenieTurnSurface | null;
  /** The newest announcement said before this speaker took the floor. */
  readonly saidBefore: number;
}

const SILENT: GenieAnnouncerText = { text: '', key: 'silent' };

let registry: SurfaceRegistry = { panel: 0, route: 0, panelOpen: false, routeAskVisible: false };
let speakerState: SpeakerState = { speaker: null, saidBefore: 0 };
/** The newest announcement a speaker has rendered. */
let said = 0;
const listeners = new Set<() => void>();

function speakerOf(current: SurfaceRegistry): GenieTurnSurface | null {
  if (current.panel > 0 && (current.panelOpen || current.route === 0)) return 'panel';
  if (current.route > 0) return 'route';
  return null;
}

function setRegistry(next: SurfaceRegistry): void {
  registry = next;
  const nextSpeaker = speakerOf(next);
  if (nextSpeaker === speakerState.speaker) return;
  speakerState = { speaker: nextSpeaker, saidBefore: said };
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

/** Called after a speaker's region committed announcement `seq`. */
function markSaid(seq: number): void {
  said = Math.max(said, seq);
}

function subscribeSpeaker(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSpeakerState(): SpeakerState {
  return speakerState;
}

const SERVER_SPEAKER_STATE: SpeakerState = { speaker: null, saidBefore: 0 };

function getServerSpeakerState(): SpeakerState {
  return SERVER_SPEAKER_STATE;
}

/** True while `/ask-genie` shows its Ask tab: a turn landing there is seen,
 *  so the launcher does not badge it. */
export function isGenieRouteAskVisible(): boolean {
  return registry.route > 0 && registry.routeAskVisible;
}

/**
 * What `surface`'s region says. `visible` is the panel's open state, or
 * whether the route's Ask tab is shown.
 */
export function useGenieAnnouncer(surface: GenieTurnSurface, visible: boolean): GenieAnnouncerText {
  useEffect(() => registerSurface(surface), [surface]);
  useEffect(() => {
    setSurfaceVisible(surface, visible);
  }, [surface, visible]);
  const { speaker, saidBefore } = useSyncExternalStore(subscribeSpeaker, getSpeakerState, getServerSpeakerState);
  const { inFlight, announcement, announcementSeq, announcedDuringTurn } = useGenieTurn();
  const speaking = speaker === surface;
  const news = speaking && announcement !== '' && announcementSeq > saidBefore;
  const label = inFlight ? genieProgressLabel(inFlight.progress) : '';
  // Mid-turn, the announcement holds only while the stage it was made in does.
  const holdsStage =
    inFlight === null ||
    (announcedDuringTurn !== null &&
      announcedDuringTurn.generation === inFlight.generation &&
      genieProgressLabel(announcedDuringTurn.progress) === label);
  const saying = news && holdsStage ? announcementSeq : null;
  // Layout phase: marked before any passive effect of the same commit can
  // hand the floor to the other surface.
  useLayoutEffect(() => {
    if (saying !== null) markSaid(saying);
  }, [saying]);
  if (!speaking) return SILENT;
  if (saying !== null) return { text: announcement, key: `announcement-${saying}` };
  return inFlight ? { text: label, key: 'stage' } : SILENT;
}

export function __resetGenieAnnouncerForTests(): void {
  registry = { panel: 0, route: 0, panelOpen: false, routeAskVisible: false };
  said = getGenieTurnSnapshot().announcementSeq;
  speakerState = { speaker: null, saidBefore: said };
  for (const listener of listeners) listener();
}
