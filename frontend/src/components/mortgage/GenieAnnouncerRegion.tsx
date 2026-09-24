import { Fragment } from 'react';
import type { GenieTurnSurface } from '../../lib/genieInFlightTurn';
import { useGenieAnnouncer } from './useGenieAnnouncer';

/**
 * A Genie surface's ONE persistent sr-only polite region (audit 2026-09-21
 * `a11y-06`); useGenieAnnouncer decides what it says and which surface
 * speaks. The keyed fragment replaces the text node whenever the text is news
 * (the same "SQL copied" twice is inserted, and spoken, twice), while a stage
 * label that holds is never touched. `visible` is the panel's open state, or
 * whether the route's Ask tab is shown.
 */
export function GenieAnnouncerRegion({ surface, visible }: { surface: GenieTurnSurface; visible: boolean }) {
  const { text, key } = useGenieAnnouncer(surface, visible);
  return (
    <div className="sr-only" role="status" aria-live="polite" data-genie-announcer={surface}>
      <Fragment key={key}>{text}</Fragment>
    </div>
  );
}
