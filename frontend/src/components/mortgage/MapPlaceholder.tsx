import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon } from '../Icon';
import { Chip } from '../Primitives';

// Shape of the @svg-maps/usa default export (see vite-env.d.ts).
interface UsaSvgMapLocation { name: string; id: string; path: string }
interface UsaSvgMap { label: string; viewBox: string; locations: UsaSvgMapLocation[] }

/**
 * USChoroplethMap — real interactive US state map with click-to-drill.
 *
 * Matches the prototype's `ChoroplethMap`:
 *   - .map-wrap chassis with breadcrumbs, drill-hint chip, legend, map-tip.
 *   - map-region lvl-1..4 fills (color-mix with --accent), is-selected accent.
 *   - level state: 'state' → 'county' → 'zip' → borrower deep-link.
 *   - demo drill path: US → Georgia → Atlanta MSA → 30305/30309/30324/30339.
 *
 * Upgrade vs. prototype: the prototype used hand-drawn stylized polygons. We
 * use @svg-maps/usa (Albers USA pre-projected paths, ~141 KB raw / ~30 KB
 * gzipped) for real US geography so it reads as a product, not a sketch.
 *
 * TODO: when the borrower dataset expansion slice lands, derive STATE_FACTS
 * from mocks/demoData.ts instead of hardcoded synthetic counts.
 */

// ---------- Synthetic per-state facts (demo-only; see TODO above) ----------

interface StateFacts {
  count: number;
  avgScore: number;
  lvl: 1 | 2 | 3 | 4;
  topSegment: string;
}

// lowercase state id (matches @svg-maps/usa) → facts. Weighted so GA, CA, TX,
// FL stand out — GA/30309 is the canonical demo borrower.
const STATE_FACTS: Record<string, StateFacts> = {
  ga: { count: 1200, avgScore: 84, lvl: 4, topSegment: 'In the Money' },
  ca: { count: 2140, avgScore: 83, lvl: 4, topSegment: 'In the Money' },
  tx: { count: 1810, avgScore: 82, lvl: 4, topSegment: 'Home Equity' },
  fl: { count: 1505, avgScore: 81, lvl: 4, topSegment: 'Investor' },
  ny: { count: 820,  avgScore: 79, lvl: 3, topSegment: 'Retention' },
  il: { count: 690,  avgScore: 76, lvl: 3, topSegment: 'In the Money' },
  nc: { count: 710,  avgScore: 78, lvl: 3, topSegment: 'Permit Activity' },
  va: { count: 640,  avgScore: 77, lvl: 3, topSegment: 'In the Money' },
  oh: { count: 620,  avgScore: 76, lvl: 3, topSegment: 'In the Money' },
  pa: { count: 540,  avgScore: 76, lvl: 3, topSegment: 'Retention' },
  mi: { count: 540,  avgScore: 75, lvl: 3, topSegment: 'Home Equity' },
  co: { count: 520,  avgScore: 80, lvl: 3, topSegment: 'Listed' },
  wa: { count: 500,  avgScore: 79, lvl: 3, topSegment: 'In the Money' },
  ma: { count: 480,  avgScore: 78, lvl: 3, topSegment: 'Retention' },
  az: { count: 470,  avgScore: 77, lvl: 3, topSegment: 'Permit Activity' },
  nj: { count: 410,  avgScore: 77, lvl: 3, topSegment: 'Retention' },
  tn: { count: 395,  avgScore: 73, lvl: 2, topSegment: 'In the Money' },
  mn: { count: 330,  avgScore: 71, lvl: 2, topSegment: 'In the Money' },
  in: { count: 310,  avgScore: 73, lvl: 2, topSegment: 'Home Equity' },
  sc: { count: 310,  avgScore: 73, lvl: 2, topSegment: 'In the Money' },
  wi: { count: 295,  avgScore: 72, lvl: 2, topSegment: 'Home Equity' },
  or: { count: 290,  avgScore: 74, lvl: 2, topSegment: 'Listed' },
  mo: { count: 285,  avgScore: 72, lvl: 2, topSegment: 'In the Money' },
  md: { count: 265,  avgScore: 74, lvl: 2, topSegment: 'Retention' },
  al: { count: 260,  avgScore: 72, lvl: 2, topSegment: 'Home Equity' },
  ok: { count: 240,  avgScore: 72, lvl: 2, topSegment: 'Home Equity' },
  ky: { count: 235,  avgScore: 71, lvl: 2, topSegment: 'In the Money' },
  la: { count: 230,  avgScore: 72, lvl: 2, topSegment: 'Home Equity' },
  ia: { count: 215,  avgScore: 72, lvl: 2, topSegment: 'In the Money' },
  ct: { count: 215,  avgScore: 74, lvl: 2, topSegment: 'Retention' },
  ar: { count: 205,  avgScore: 70, lvl: 2, topSegment: 'Home Equity' },
  nv: { count: 195,  avgScore: 72, lvl: 2, topSegment: 'Listed' },
  ms: { count: 185,  avgScore: 70, lvl: 2, topSegment: 'Home Equity' },
  ks: { count: 180,  avgScore: 71, lvl: 2, topSegment: 'Home Equity' },
  ut: { count: 175,  avgScore: 70, lvl: 2, topSegment: 'Permit Activity' },
  ne: { count: 150,  avgScore: 70, lvl: 1, topSegment: 'Home Equity' },
  nm: { count: 145,  avgScore: 74, lvl: 1, topSegment: 'Home Equity' },
  id: { count: 140,  avgScore: 69, lvl: 1, topSegment: 'Permit Activity' },
  mt: { count: 120,  avgScore: 68, lvl: 1, topSegment: 'Home Equity' },
  wv: { count: 115,  avgScore: 67, lvl: 1, topSegment: 'Home Equity' },
  wy: { count: 110,  avgScore: 66, lvl: 1, topSegment: 'Home Equity' },
  sd: { count: 92,   avgScore: 66, lvl: 1, topSegment: 'Home Equity' },
  nh: { count: 90,   avgScore: 69, lvl: 1, topSegment: 'Retention' },
  me: { count: 88,   avgScore: 68, lvl: 1, topSegment: 'Retention' },
  ak: { count: 85,   avgScore: 70, lvl: 1, topSegment: 'Home Equity' },
  hi: { count: 80,   avgScore: 72, lvl: 1, topSegment: 'Investor' },
  nd: { count: 78,   avgScore: 65, lvl: 1, topSegment: 'Home Equity' },
  de: { count: 65,   avgScore: 70, lvl: 1, topSegment: 'Home Equity' },
  vt: { count: 58,   avgScore: 67, lvl: 1, topSegment: 'Retention' },
  ri: { count: 48,   avgScore: 69, lvl: 1, topSegment: 'Retention' },
  dc: { count: 42,   avgScore: 82, lvl: 2, topSegment: 'Retention' },
};

// Map segment code (from activeSegs / segmentFilter) → the topSegment strings
// above. Used for highlight/dim logic on segment-intelligence.
const SEGMENT_CODE_TO_NAME: Record<string, string> = {
  itm: 'In the Money',
  listed: 'Listed',
  permit: 'Permit Activity',
  investor: 'Investor',
  equity: 'Home Equity',
  retention: 'Retention',
};

// ---------- Stylized county/ZIP drill-downs for the demo path ------------

interface DrillRegion {
  id: string;
  name: string;
  d: string;
  lvl: 1 | 2 | 3 | 4;
  count: number;
  avgScore: number;
  borrowerId?: string;
}

// Atlanta MSA counties (stylized — prototype-style polygons keyed to the GA
// demo drill). Grid laid out in a 340x310 canvas to match the prototype's
// county viewBox.
const GA_COUNTIES: DrillRegion[] = [
  { id: 'cobb',     name: 'Cobb',     d: 'M40,40 L160,35 L165,115 L45,120 Z',   lvl: 3, count: 285, avgScore: 80 },
  { id: 'gwinnett', name: 'Gwinnett', d: 'M165,35 L300,40 L305,115 L165,115 Z', lvl: 3, count: 260, avgScore: 79 },
  { id: 'fulton',   name: 'Fulton',   d: 'M40,120 L165,115 L170,220 L45,225 Z', lvl: 4, count: 520, avgScore: 86 },
  { id: 'dekalb',   name: 'DeKalb',   d: 'M170,115 L305,115 L310,220 L170,220 Z', lvl: 3, count: 310, avgScore: 81 },
  { id: 'other',    name: 'Other GA', d: 'M40,225 L310,225 L310,280 L40,280 Z', lvl: 1, count: 180, avgScore: 68 },
];

// ZIPs within Fulton County (Atlanta) — 30309 is the demo borrower anchor.
const ATL_ZIPS: DrillRegion[] = [
  { id: '30305', name: '30305', d: 'M30,30 L110,30 L110,100 L30,100 Z',   lvl: 3, count: 68, avgScore: 82 },
  { id: '30309', name: '30309', d: 'M110,30 L200,30 L200,100 L110,100 Z', lvl: 4, count: 94, avgScore: 94, borrowerId: 'B-48291' },
  { id: '30324', name: '30324', d: 'M200,30 L280,30 L280,100 L200,100 Z', lvl: 3, count: 58, avgScore: 80 },
  { id: '30339', name: '30339', d: 'M30,100 L110,100 L110,180 L30,180 Z', lvl: 3, count: 72, avgScore: 81 },
  { id: '30308', name: '30308', d: 'M110,100 L200,100 L200,180 L110,180 Z', lvl: 2, count: 46, avgScore: 75 },
  { id: '30318', name: '30318', d: 'M200,100 L280,100 L280,180 L200,180 Z', lvl: 2, count: 52, avgScore: 76 },
];

type Level = 'state' | 'county' | 'zip';

interface Selected {
  level: Level;
  id: string;
  name: string;
}

interface MapPlaceholderProps {
  height?: number;
  /** Optional segment-code filter. Non-matching states dim. */
  segmentFilter?: string[];
}

interface HoverState {
  x: number;
  y: number;
  name: string;
  count: number;
  avgScore: number;
  topSegment?: string;
}

/**
 * Main component — historical filename kept so routes don't shuffle; exported
 * as both `MapPlaceholder` (legacy) and `USChoroplethMap` (true name).
 */
export function MapPlaceholder({ height = 420, segmentFilter }: MapPlaceholderProps) {
  const [level, setLevel] = useState<Level>('state');
  const [selected, setSelected] = useState<Selected | null>(null);
  const [hover, setHover] = useState<HoverState | null>(null);
  const [usaMap, setUsaMap] = useState<UsaSvgMap | null>(null);
  const navigate = useNavigate();

  // Lazy-load the @svg-maps/usa data so the ~140 KB of path strings lands
  // in its own code-split chunk instead of the main bundle.
  useEffect(() => {
    let cancelled = false;
    import('@svg-maps/usa').then((mod) => {
      if (!cancelled) setUsaMap(mod.default as UsaSvgMap);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const activeSegNames = useMemo(() => {
    if (!segmentFilter || segmentFilter.length === 0) return null;
    return new Set(
      segmentFilter.map((c) => SEGMENT_CODE_TO_NAME[c]).filter((n): n is string => Boolean(n)),
    );
  }, [segmentFilter]);

  const totalCount = useMemo(() => {
    if (level === 'state') {
      return Object.values(STATE_FACTS).reduce((a, b) => a + b.count, 0);
    }
    if (level === 'county') {
      return GA_COUNTIES.reduce((a, b) => a + b.count, 0);
    }
    return ATL_ZIPS.reduce((a, b) => a + b.count, 0);
  }, [level]);

  // ----- STATE level: real US paths via @svg-maps/usa ----------------------
  const renderStateLevel = () => {
    if (!usaMap) {
      return (
        <div
          style={{
            marginTop: 36,
            height: 'calc(100% - 36px)',
            display: 'grid',
            placeItems: 'center',
            color: 'var(--text-3)',
            fontSize: 12,
          }}
        >
          Loading geography…
        </div>
      );
    }
    return (
    <svg
      viewBox={usaMap.viewBox}
      preserveAspectRatio="xMidYMid meet"
      style={{ marginTop: 36, height: 'calc(100% - 36px)' }}
    >
      {usaMap.locations.map((loc) => {
        const facts = STATE_FACTS[loc.id];
        const lvl = facts?.lvl ?? 1;
        const dim = activeSegNames !== null && facts && !activeSegNames.has(facts.topSegment);
        const classes = [
          'map-region',
          facts ? `lvl-${lvl}` : '',
          selected?.level === 'state' && selected.id === loc.id ? 'is-selected' : '',
        ]
          .filter(Boolean)
          .join(' ');
        return (
          <path
            key={loc.id}
            d={loc.path}
            className={classes}
            style={dim ? { opacity: 0.3 } : undefined}
            aria-label={loc.name}
            onMouseEnter={(e) =>
              facts &&
              setHover({
                x: e.clientX,
                y: e.clientY,
                name: loc.name,
                count: facts.count,
                avgScore: facts.avgScore,
                topSegment: facts.topSegment,
              })
            }
            onMouseMove={(e) =>
              setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
            }
            onMouseLeave={() => setHover(null)}
            onClick={() => {
              if (loc.id === 'ga') {
                setLevel('county');
                setSelected({ level: 'state', id: 'ga', name: 'Georgia' });
              } else if (facts) {
                setSelected({ level: 'state', id: loc.id, name: loc.name });
              }
            }}
          />
        );
      })}
      {/* Pulse beacon over Georgia to telegraph the demo drill */}
      <GeorgiaBeacon />
    </svg>
    );
  };

  // ----- COUNTY level: Atlanta MSA counties --------------------------------
  const renderCountyLevel = () => (
    <svg
      viewBox="0 0 340 310"
      preserveAspectRatio="xMidYMid meet"
      style={{ marginTop: 36, height: 'calc(100% - 36px)' }}
    >
      {GA_COUNTIES.map((c) => {
        const isFulton = c.id === 'fulton';
        const classes = [
          'map-region',
          `lvl-${c.lvl}`,
          selected?.level === 'county' && selected.id === c.id ? 'is-selected' : '',
        ]
          .filter(Boolean)
          .join(' ');
        return (
          <g key={c.id}>
            <path
              d={c.d}
              className={classes}
              onMouseEnter={(e) =>
                setHover({
                  x: e.clientX,
                  y: e.clientY,
                  name: `${c.name} County, GA`,
                  count: c.count,
                  avgScore: c.avgScore,
                })
              }
              onMouseMove={(e) =>
                setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
              }
              onMouseLeave={() => setHover(null)}
              onClick={() => {
                if (c.id === 'fulton') {
                  setLevel('zip');
                  setSelected({ level: 'county', id: 'fulton', name: 'Fulton County' });
                } else {
                  setSelected({ level: 'county', id: c.id, name: c.name });
                }
              }}
            />
            {isFulton && (
              <g pointerEvents="none">
                <circle cx="105" cy="170" r="6" fill="var(--accent)" fillOpacity="0.25">
                  <animate
                    attributeName="r"
                    from="4"
                    to="18"
                    dur="1.8s"
                    repeatCount="indefinite"
                  />
                  <animate
                    attributeName="fill-opacity"
                    from="0.35"
                    to="0"
                    dur="1.8s"
                    repeatCount="indefinite"
                  />
                </circle>
                <circle cx="105" cy="170" r="3" fill="var(--accent)" />
              </g>
            )}
          </g>
        );
      })}
    </svg>
  );

  // ----- ZIP level: Atlanta ZIPs; click drills to borrower 360 --------------
  const renderZipLevel = () => (
    <svg
      viewBox="0 0 310 210"
      preserveAspectRatio="xMidYMid meet"
      style={{ marginTop: 36, height: 'calc(100% - 36px)' }}
    >
      {ATL_ZIPS.map((z) => {
        const classes = [
          'map-region',
          `lvl-${z.lvl}`,
          selected?.level === 'zip' && selected.id === z.id ? 'is-selected' : '',
        ]
          .filter(Boolean)
          .join(' ');
        return (
          <g key={z.id}>
            <path
              d={z.d}
              className={classes}
              onMouseEnter={(e) =>
                setHover({
                  x: e.clientX,
                  y: e.clientY,
                  name: `ZIP ${z.name}, Atlanta GA`,
                  count: z.count,
                  avgScore: z.avgScore,
                })
              }
              onMouseMove={(e) =>
                setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
              }
              onMouseLeave={() => setHover(null)}
              onClick={() => {
                setSelected({ level: 'zip', id: z.id, name: z.name });
                if (z.borrowerId) {
                  navigate(`/borrower-360/${z.borrowerId}`);
                }
              }}
            />
            {/* ZIP label */}
            <text
              x={extractX(z.d) + 35}
              y={extractY(z.d) + 38}
              fontSize="12"
              fontFamily="var(--font-mono)"
              fill="var(--text-1)"
              pointerEvents="none"
            >
              {z.name}
            </text>
          </g>
        );
      })}
    </svg>
  );

  return (
    <div className="map-wrap" style={{ height }}>
      {/* Breadcrumbs */}
      <div
        style={{
          position: 'absolute',
          top: 12,
          left: 14,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          zIndex: 2,
        }}
      >
        <div className="eyebrow">Geography drill-down</div>
        <div className="topbar__crumbs" style={{ fontSize: 12 }}>
          <button
            type="button"
            className={`filter ${level === 'state' ? 'is-active' : ''}`}
            style={{ padding: '3px 8px' }}
            onClick={() => {
              setLevel('state');
              setSelected(null);
            }}
          >
            <span className="filter__value">US</span>
          </button>
          {level !== 'state' && (
            <>
              <Icon name="chevright" size={11} />
              <button
                type="button"
                className={`filter ${level === 'county' ? 'is-active' : ''}`}
                style={{ padding: '3px 8px' }}
                onClick={() => {
                  setLevel('county');
                  setSelected({ level: 'state', id: 'ga', name: 'Georgia' });
                }}
              >
                <span className="filter__value">Georgia</span>
              </button>
            </>
          )}
          {level === 'zip' && (
            <>
              <Icon name="chevright" size={11} />
              <span className="filter is-active" style={{ padding: '3px 8px' }}>
                <span className="filter__value">Atlanta MSA</span>
              </span>
            </>
          )}
        </div>
      </div>

      {/* Drill hint chip */}
      <div style={{ position: 'absolute', top: 12, right: 14, zIndex: 2 }}>
        <Chip variant="neutral" icon="pin">
          {level === 'state'
            ? 'Click Georgia to drill'
            : level === 'county'
              ? 'Click Fulton to drill'
              : 'Click a ZIP to open borrower'}
        </Chip>
      </div>

      {level === 'state' && renderStateLevel()}
      {level === 'county' && renderCountyLevel()}
      {level === 'zip' && renderZipLevel()}

      {/* Legend */}
      <div className="map-legend">
        <div>
          Borrowers in selection{' '}
          <span
            style={{
              color: 'var(--text-1)',
              fontFamily: 'var(--font-mono)',
              marginLeft: 6,
            }}
          >
            {totalCount.toLocaleString()}
          </span>
        </div>
        <div className="map-legend__bar">
          <span style={{ background: 'var(--bg-3)' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 15%, var(--bg-3))' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 30%, var(--bg-3))' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 50%, var(--bg-3))' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 70%, var(--bg-3))' }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10 }}>
          <span>Lower</span>
          <span>Higher</span>
        </div>
      </div>

      {/* Hover tooltip */}
      {hover && (
        <div
          className="map-tip"
          style={{
            left: Math.max(120, Math.min(window.innerWidth - 200, hover.x)),
            top: hover.y - 4,
          }}
        >
          <div className="map-tip__name">{hover.name}</div>
          <div className="map-tip__row">
            <span>Borrowers</span>
            <span className="v num">{hover.count.toLocaleString()}</span>
          </div>
          <div className="map-tip__row">
            <span>Avg. score</span>
            <span className="v num">{hover.avgScore}</span>
          </div>
          {hover.topSegment && (
            <div className="map-tip__row">
              <span>Top segment</span>
              <span className="v">{hover.topSegment}</span>
            </div>
          )}
          <div
            className="map-tip__row"
            style={{ marginTop: 4, borderTop: '1px dashed var(--line-1)', paddingTop: 4 }}
          >
            <span>Source</span>
            <span className="v mono" style={{ fontSize: 10 }}>
              CLIP + MMA
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export { MapPlaceholder as USChoroplethMap };

// ---------- Helpers -------------------------------------------------------

/**
 * Pulse beacon rendered over Georgia at the state level. The x/y coords are
 * in @svg-maps/usa units (viewBox "192 9 1028 746"). GA's path bbox centers
 * around x≈1004 y≈415 in that projection (verified at runtime).
 */
function GeorgiaBeacon() {
  return (
    <g pointerEvents="none">
      <circle cx="1004" cy="415" r="10" fill="var(--accent)" fillOpacity="0.2">
        <animate attributeName="r" from="6" to="24" dur="1.8s" repeatCount="indefinite" />
        <animate
          attributeName="fill-opacity"
          from="0.35"
          to="0"
          dur="1.8s"
          repeatCount="indefinite"
        />
      </circle>
      <circle cx="1004" cy="415" r="4" fill="var(--accent)" />
    </g>
  );
}

/** Extract the first "Mx,y" coord from an SVG path `d` attribute. */
function extractX(d: string): number {
  const match = d.match(/M\s*(-?\d+(?:\.\d+)?)/);
  return match ? parseFloat(match[1]) : 0;
}
function extractY(d: string): number {
  const match = d.match(/M\s*-?\d+(?:\.\d+)?[, ]\s*(-?\d+(?:\.\d+)?)/);
  return match ? parseFloat(match[1]) : 0;
}
