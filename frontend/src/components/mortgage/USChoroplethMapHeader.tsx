/**
 * USChoroplethMapHeader — the geography map's `.map-hdr`: the breadcrumb
 * trail (US > drilled state), the coverage / ZIP-gap chips, the Borrowers |
 * Unattended leads colouring toggle, the "View as table" switch, the S9
 * "Start campaign" action and "Ask Genie about this state". Extracted from
 * USChoroplethMap.tsx (file-size gate); markup, class names and copy of the
 * existing controls are unchanged.
 */
import { useEffect, useRef } from 'react';
import { Icon } from '../Icon';
import { Chip } from '../Primitives';
import { genieStatePrompt } from '../../lib/genieContext';
import { GenieAskAbout } from './GenieAskAbout';
import { claimDrillFocus, drillExitOriginatedInMap } from './USChoroplethMap.a11y';
import { formatCount } from '../../lib/formatters';

export type MapView = 'map' | 'table';

interface USChoroplethMapHeaderProps {
  drilled: boolean;
  drillStateUC: string;
  drillStateName: string;
  onBackToUs: () => void;
  /** Distinct ZIPs in coverage (backend scope), or null while loading. */
  coverageZipCount: number | null;
  /** False while the stage is warming up or failed: no state is drawn to click. */
  drillHint: boolean;
  /** Borrowers in the drilled state the ZIP layer cannot show. */
  zipUnassigned: number;
  overlayOn: boolean;
  setOverlayOn: (on: boolean) => void;
  view: MapView;
  setView: (view: MapView) => void;
  campaignPrefillPath: string | null;
  onStartCampaign: (path: string) => void;
}

export function USChoroplethMapHeader({
  drilled,
  drillStateUC,
  drillStateName,
  onBackToUs,
  coverageZipCount,
  drillHint,
  zipUnassigned,
  overlayOn,
  setOverlayOn,
  view,
  setView,
  campaignPrefillPath,
  onStartCampaign,
}: USChoroplethMapHeaderProps) {
  // A control that ends the drill can remove itself (Segment Intelligence's
  // "Clear geography", a vanished ZIP tile on Back), and focus would fall to
  // <body>. It lands on the US crumb, the national view now on screen, but
  // only when the drill was ended from the map or from a control marked as
  // the map's (MAP_DRILL_EXIT_ATTR): a page-level "Clear filters" that
  // disables itself must not send focus, and the scroll, down to the map.
  // Focus the user still holds elsewhere is never taken (claimDrillFocus).
  const usCrumbRef = useRef<HTMLButtonElement | null>(null);
  const lastFocused = useRef<Element | null>(null);
  useEffect(() => {
    const doc = usCrumbRef.current?.ownerDocument;
    if (!doc) return undefined;
    const onFocusIn = (event: FocusEvent) => {
      lastFocused.current = event.target instanceof Element ? event.target : null;
    };
    doc.addEventListener('focusin', onFocusIn, true);
    return () => doc.removeEventListener('focusin', onFocusIn, true);
  }, []);
  const wasDrilled = useRef(drilled);
  useEffect(() => {
    if (wasDrilled.current && !drilled) {
      const crumb = usCrumbRef.current;
      if (drillExitOriginatedInMap(lastFocused.current, crumb?.closest('.map-wrap') ?? null)) {
        claimDrillFocus(crumb);
      }
    }
    wasDrilled.current = drilled;
  }, [drilled]);

  return (
    <div className="map-hdr">
      {/* Breadcrumbs */}
      <div className="map-crumbs">
        <div className="eyebrow">Geography drill-down</div>
        <div className="topbar__crumbs map-crumbs__trail">
          {/* US is the single back step — with the county rung gone,
              zip -> state IS zip -> national. */}
          <button
            ref={usCrumbRef}
            type="button"
            className={`filter filter--compact ${drilled ? '' : 'is-active'}`}
            onClick={onBackToUs}
          >
            <span className="filter__value">US</span>
          </button>
          {drilled && (
            <>
              <Icon name="chevright" size={11} />
              <span className="filter filter--compact is-active">
                <span className="filter__value">{drillStateName || 'State'}</span>
              </span>
            </>
          )}
        </div>
      </div>

      {/* Drill hint chip + optional Cotality coverage chip. The scope copy
          comes from backend-discovered gold rollups instead of a fixed demo
          statement. No "click a state" while the stage draws no states. */}
      <div className="map-corner-chips">
        <Chip variant="neutral" icon="pin">
          {!drilled
            ? coverageZipCount
              ? `${formatCount(coverageZipCount)} ZIPs${drillHint ? ' · click a state to drill' : ''}`
              : 'Loading coverage…'
            : `ZIPs in ${drillStateName || 'state'}`}
        </Chip>
        {/* Drill-gap disclosure: the ZIP tiles below sum to LESS than the
            state tile the user just clicked, because the share carries no
            usable ZIP for these borrowers. Say it where the gap appears. */}
        {drilled && zipUnassigned > 0 && (
          <Chip variant="neutral" icon="db">
            {formatCount(zipUnassigned)} borrowers without ZIP assignment
          </Chip>
        )}
        {/* S9 overlay toggle — two .filter-style buttons in the prototype's
            vocabulary. "Borrowers" is the default; "Unattended leads"
            recolours + extends the tooltip from the assigned-vs-unattended
            overlay. */}
        <div className="map-overlay-toggle" role="group" aria-label="Map coloring">
          <button
            type="button"
            className={`filter filter--compact ${overlayOn ? '' : 'is-active'}`}
            aria-pressed={!overlayOn}
            onClick={() => setOverlayOn(false)}
          >
            <span className="filter__value">Borrowers</span>
          </button>
          <button
            type="button"
            className={`filter filter--compact ${overlayOn ? 'is-active' : ''}`}
            aria-pressed={overlayOn}
            onClick={() => setOverlayOn(true)}
          >
            <span className="filter__value">Unattended leads</span>
          </button>
        </div>
        {/* The label names the view it switches to (the rate-window pattern:
            one mechanism, no aria-pressed on top). */}
        <button
          type="button"
          className="filter filter--compact map-view-toggle"
          onClick={() => setView(view === 'map' ? 'table' : 'map')}
        >
          <span className="filter__value">{view === 'map' ? 'View as table' : 'View as map'}</span>
        </button>
        {campaignPrefillPath && (
          <button
            type="button"
            className="btn btn--primary btn--sm map-start-campaign"
            onClick={() => onStartCampaign(campaignPrefillPath)}
          >
            <Icon name="send" size={12} />
            Start campaign
          </button>
        )}
        {/* "Ask Genie about this state" (genie-04) on the drilled state:
            the USPS code is the only thing that reaches the template. The
            hover tooltip is not interactive, so the drilled view is where
            the entry point lives. */}
        {drilled && drillStateUC !== '' && (
          <GenieAskAbout prompt={genieStatePrompt(drillStateUC)} subject={`state: ${drillStateName}`} />
        )}
      </div>
    </div>
  );
}
