/**
 * USChoroplethMapZctaLevel — the ZIP rung as a map (audit dataviz-01 /
 * visual-09, D-dataviz-geo-e part B; motion-10's drill continuity).
 *
 * deviation:zcta-level (product). The prototype draws the ZIP level as
 * `.map-region` polygons (design_files/Module 0 Prototype.html:1797-1840,
 * TRAVIS_ZIPS) with the ramp of :860-867 and the drill hint of :1637; the
 * shipped rung was a densest-24 tile grid over an empty stage. This rung
 * draws the drilled state's committed Census 2020 ZCTAs (zctaGeometry, a
 * same-origin hashed asset fetched only on the drill):
 *
 *  - every ZCTA fill first, sorted by ZIP; the state's 500k outline ABOVE the
 *    fills; then the labels. A ZCTA with borrowers in the selection (the d2
 *    rule, never the fill value) is a control: role=button, one roving tab
 *    stop, the hover card, and a click / Enter / Space that opens its
 *    single-ZIP Lead Queue (the parent's audited onSelectZip). Every other
 *    ZCTA is muted context, hidden from assistive technology and the pointer.
 *  - labels only on populated ZCTAs at least 36px wide at the committed zoom,
 *    re-placed at a gesture's end, counter-scaled to their token size.
 *  - pan and zoom on the viewBox (useSvgViewBox): ctrl/cmd-wheel, pinch,
 *    drag, + - 0 keys, and the header's Zoom in / Zoom out / Fit buttons
 *    (WCAG 2.5.7), registered once; the first frame tweens from the national
 *    view to the fit (instant under reduced motion).
 *  - one native keydown and one native click listener on the stage (roving,
 *    activation, zoom keys), so the paths carry no handlers; Escape stays
 *    with .map-levels (card first, then up a level).
 *  - the reconcile note names the ZIPs with borrowers and no ZIP-area
 *    boundary (PO-box and unique ZIPs) and the borrowers with no ZIP.
 *
 * The densest-ZIP tiles are the degraded fallback, never a mock: a state
 * with no committed file, or a geometry read that fails after its retries,
 * reports 'tiles' and its host (USChoroplethMapZipAreas) draws them with a
 * status line, so the legend's scale and caption follow what is on screen.
 *
 * Bundling: this chunk imports nothing from the map's own modules (the
 * shared a11y helpers arrive as `kit`, the unit lookup is inline). A chunk
 * reaching USChoroplethMap.a11y made the bundler split that module out of
 * the map chunk, adding a chunk to Home's closure and a preload entry to the
 * initial bundle.
 */
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import { formatCount } from '../../lib/formatters';
import { safeSegmentName } from '../../lib/segmentMetadata';
import type * as MapA11y from './USChoroplethMap.a11y';
import type { ZipStageProps } from './USChoroplethMap.zipStage';
import { classify } from './USChoroplethMap.scale';
import { MAX_ZOOM, useSvgViewBox, viewBoxAttr, type ViewBox } from './useSvgViewBox';
import { hasZctaGeometry, loadZctaGeometry, zctaGeometryKey, type ZctaGeometry } from './zctaGeometry';
import {
  boxView,
  fitZctaView,
  isPopulated,
  labelledZips,
  parseViewBox,
  screenScale,
  zipsWithoutBoundary,
} from './zctaLevel.logic';
import './USChoroplethMapZctaLevel.css';

export type ZipRung = 'polygons' | 'tiles';

/** The map's keyboard and naming helpers, handed down by the host (see Bundling above). */
export type ZctaKit = Pick<typeof MapA11y, 'claimDrillFocus' | 'moveRovingFocus' | 'zipAriaLabel'>;

const UNIT_ATTR = 'data-map-unit';
const POPULATED_ATTR = 'data-populated';

export interface USChoroplethMapZctaLevelProps extends Omit<ZipStageProps, 'onView'> {
  kit: ZctaKit;
  /** Which rung is on screen, once it is known ('tiles': the host draws the fallback). */
  onRung: (rung: ZipRung) => void;
  /** The header's zoom buttons while the polygons show; null when they go. */
  onZoomControls: (controls: ReactNode) => void;
}

export function USChoroplethMapZctaLevel({ usps, onRung, ...stage }: USChoroplethMapZctaLevelProps) {
  const available = hasZctaGeometry(usps);
  const geometry = useQuery({
    queryKey: zctaGeometryKey(usps),
    queryFn: ({ signal }) => loadZctaGeometry(usps, signal),
    enabled: available,
    staleTime: Infinity,
    retry: 2,
  });
  const fallback = !available || geometry.isError;
  const data = fallback ? null : geometry.data ?? null;
  useLayoutEffect(() => {
    if (fallback) onRung('tiles');
    else if (data) onRung('polygons');
  }, [data, fallback, onRung]);
  if (fallback) return null;
  if (!data) return <div className="map-stage map-stage--empty map-stage--zcta">Loading ZIP areas…</div>;
  return <ZctaStage {...stage} geometry={data} />;
}

interface ZoomState {
  canIn: boolean;
  canOut: boolean;
}

interface ZoomStore {
  get: () => ZoomState;
  set: (next: ZoomState) => void;
  subscribe: (listener: () => void) => () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  fit: () => void;
}

function createZoomStore(actions: Pick<ZoomStore, 'zoomIn' | 'zoomOut' | 'fit'>): ZoomStore {
  let state: ZoomState = { canIn: true, canOut: false };
  const listeners = new Set<() => void>();
  return {
    ...actions,
    get: () => state,
    set: (next) => {
      if (next.canIn === state.canIn && next.canOut === state.canOut) return;
      state = next;
      listeners.forEach((listener) => listener());
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/** The header's three buttons; only they re-render when a gesture ends at a limit. */
function MapZoomButtons({ store }: { store: ZoomStore }) {
  const { canIn, canOut } = useSyncExternalStore(store.subscribe, store.get);
  return (
    <span className="map-zoom" role="group" aria-label="Map zoom">
      <button
        type="button"
        className="btn btn--ghost btn--sm map-zoom__btn"
        aria-label="Zoom in"
        aria-disabled={!canIn || undefined}
        onClick={() => canIn && store.zoomIn()}
      >
        +
      </button>
      <button
        type="button"
        className="btn btn--ghost btn--sm map-zoom__btn"
        aria-label="Zoom out"
        aria-disabled={!canOut || undefined}
        onClick={() => canOut && store.zoomOut()}
      >
        −
      </button>
      <button type="button" className="btn btn--ghost btn--sm map-zoom__btn" aria-label="Fit to the populated ZIP areas" onClick={() => store.fit()}>
        Fit
      </button>
    </span>
  );
}

type StageProps = Omit<USChoroplethMapZctaLevelProps, 'usps' | 'onRung'> & { geometry: ZctaGeometry };

function ZctaStage({
  kit,
  geometry,
  nationalViewBox,
  onZoomControls,
  drillStateName,
  byZip,
  stateFacts,
  scale,
  overlayActive,
  overlayByUnit,
  selectedZip,
  autoFocus = false,
  onAutoFocused,
  hover,
  onSelectZip,
  onOpenStateQueue,
}: StageProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const noteActionRef = useRef<HTMLButtonElement | null>(null);
  const fit = useMemo<ViewBox>(() => fitZctaView(geometry.areas, byZip, geometry.stateBox), [byZip, geometry]);
  const bounds = useMemo<ViewBox>(() => boxView(geometry.stateBox), [geometry]);
  // Stable by value: the tween runs once per drill.
  const from = useMemo<ViewBox | null>(() => parseViewBox(nationalViewBox), [nationalViewBox]);
  const view = useSvgViewBox({ svgRef, fit, bounds, from });
  const { onKey } = view;
  const [store] = useState(() => createZoomStore({ zoomIn: view.zoomIn, zoomOut: view.zoomOut, fit: view.fitView }));
  useEffect(() => {
    store.set({ canIn: view.zoom < MAX_ZOOM - 1e-6, canOut: view.zoom > 1 + 1e-6 });
  }, [store, view.zoom]);
  useEffect(() => {
    onZoomControls(<MapZoomButtons store={store} />);
    return () => onZoomControls(null);
  }, [onZoomControls, store]);

  // Screen px per unit and the label token, measured at each gesture's end.
  const [measure, setMeasure] = useState({ scale: 0, fontPx: 0 });
  const { committed } = view;
  useLayoutEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const fontPx = Number.parseFloat(window.getComputedStyle(svg).getPropertyValue('--fs-11')) || 0;
    setMeasure({ scale: screenScale(committed, rect.width, rect.height), fontPx });
  }, [committed]);

  const [activeZip, setActiveZip] = useState<string | null>(null);
  const isStop = (zip: string | null) => zip !== null && geometry.byZip.has(zip) && isPopulated(byZip[zip]);
  const tabStopZip = [activeZip, selectedZip].find(isStop)
    ?? geometry.areas.find((area) => isPopulated(byZip[area.zip]))?.zip
    ?? null;
  const stageHandlers = hover.handlers('zip', setActiveZip);

  // One native keydown and one native click listener on the stage.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;
    const populatedUnit = (target: EventTarget | null) => {
      const unit = target instanceof Element ? target.closest(`[${UNIT_ATTR}][${POPULATED_ATTR}]`) : null;
      return unit && svg.contains(unit) ? unit.getAttribute(UNIT_ATTR) : null;
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (onKey(event.key)) {
        event.preventDefault();
        return;
      }
      const zip = populatedUnit(event.target);
      if (zip && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        onSelectZip(zip);
        return;
      }
      kit.moveRovingFocus(event);
    };
    const onClick = (event: MouseEvent) => {
      const zip = populatedUnit(event.target);
      if (zip) onSelectZip(zip);
    };
    svg.addEventListener('keydown', onKeyDown);
    svg.addEventListener('click', onClick);
    return () => {
      svg.removeEventListener('keydown', onKeyDown);
      svg.removeEventListener('click', onClick);
    };
  }, [kit, onKey, onSelectZip]);

  useEffect(() => {
    if (!autoFocus) return;
    const stop = svgRef.current?.querySelector<SVGElement>(`[${UNIT_ATTR}][tabindex="0"]`) ?? null;
    const target = stop ?? noteActionRef.current;
    if (!target) return;
    kit.claimDrillFocus(target);
    onAutoFocused?.();
  }, [autoFocus, kit, onAutoFocused]);

  const labels = labelledZips(geometry.areas, byZip, measure.scale);
  const missing = zipsWithoutBoundary(byZip, geometry);
  const unassigned = stateFacts?.zip_unassigned_count ?? 0;
  const stateTotal = stateFacts?.addressable ?? null;
  return (
    <>
      <svg
        ref={svgRef}
        className="map-svg-stage map-zcta"
        viewBox={viewBoxAttr(committed)}
        preserveAspectRatio="xMidYMid meet"
        role="group"
        aria-label={`ZIP areas in ${drillStateName}`}
        {...stageHandlers}
      >
        {geometry.areas.map((area) => {
          const rollup = byZip[area.zip];
          if (!isPopulated(rollup)) {
            return <path key={area.zip} d={area.d} className="map-region is-empty map-region--context" aria-hidden="true" />;
          }
          const count = rollup?.addressable_borrowers ?? null;
          const unattended = overlayActive ? overlayByUnit[area.zip]?.unattended_count ?? null : null;
          const cls = classify(scale, overlayActive ? unattended : count);
          const classes = ['map-region', cls === null ? 'is-empty' : `has-data lvl-${cls}`, selectedZip === area.zip ? 'is-selected' : '']
            .filter(Boolean)
            .join(' ');
          const topSegCode = rollup?.top_segment_code ?? null;
          return (
            <path
              key={area.zip}
              d={area.d}
              className={classes}
              role="button"
              tabIndex={area.zip === tabStopZip ? 0 : -1}
              data-map-unit={area.zip}
              data-populated=""
              data-map-class={cls ?? 0}
              data-target-size-exempt="geographic-shape"
              aria-keyshortcuts="Enter"
              aria-label={kit.zipAriaLabel(area.zip, {
                count,
                avgScore: rollup?.avg_opportunity_score ?? null,
                topSegment: topSegCode ? (safeSegmentName(topSegCode) ?? undefined) : undefined,
                unattended: overlayActive ? unattended : undefined,
              })}
            />
          );
        })}
        <path className="map-zcta__outline" d={geometry.outline} aria-hidden="true" />
        {measure.fontPx > 0 && measure.scale > 0 && (
          <g
            className="map-labels"
            aria-hidden="true"
            fontSize={measure.fontPx / measure.scale}
            strokeWidth={(measure.fontPx / 4) / measure.scale}
          >
            {labels.map((area) => {
              const rollup = byZip[area.zip];
              const value = overlayActive ? overlayByUnit[area.zip]?.unattended_count ?? null : rollup?.addressable_borrowers ?? null;
              const cls = classify(scale, value);
              return area.labelAt && cls !== null ? (
                <text key={area.zip} x={area.labelAt[0]} y={area.labelAt[1]} className={`map-label map-label--lvl-${cls}`}>
                  {area.zip}
                </text>
              ) : null;
            })}
          </g>
        )}
      </svg>
      {(missing.zips > 0 || unassigned > 0) && (
        <div className="zip-tiles__reconcile text-2" role="note">
          {missing.zips > 0 && (
            <span>
              {missing.zips === 1
                ? `1 ZIP (${formatCount(missing.borrowers)} borrowers) has no Census ZIP-area boundary (PO box or unique ZIP); the table view lists it.`
                : `${formatCount(missing.zips)} ZIPs (${formatCount(missing.borrowers)} borrowers) have no Census ZIP-area boundary (PO box or unique ZIPs); the table view lists them.`}
            </span>
          )}
          {unassigned > 0 && (
            <span>
              {' '}
              {formatCount(unassigned)} borrowers in {drillStateName} carry no ZIP and appear in no ZIP area.
            </span>
          )}
          <button ref={noteActionRef} type="button" className="btn btn--ghost btn--sm" onClick={onOpenStateQueue}>
            Open all {stateTotal !== null ? formatCount(stateTotal) : ''} in Lead Queue
          </button>
        </div>
      )}
    </>
  );
}
