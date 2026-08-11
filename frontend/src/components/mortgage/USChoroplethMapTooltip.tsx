import { createPortal } from 'react-dom';
import type { HoverState } from './USChoroplethMap.utils';

/**
 * Floating map hover card. BEM block: `map-tip`.
 *
 * DESIGN-CONTRACT DEVIATION (called out per CLAUDE.md "deviations must be
 * called out"): the prototype's `map-tip` block in
 * `design_files/Module 0 Prototype.html` (see `.map-tip__name` /
 * `.map-tip__row` / `.v`, ~lines 1875-1878 of that file) only documents a
 * name + flat label/value rows. This component extends that vocabulary with
 * `map-tip__kpis` / `map-tip__kpi` / `map-tip__kpi-label` / `map-tip__kpi-value`
 * (a two-up infographic KPI grid) and `map-tip__seg` / `map-tip__seg-label` /
 * `map-tip__seg-value` (a top-segment row). The extension is intentional —
 * the real product surfaces richer per-region facts (marketable borrowers,
 * avg. opportunity score, top segment) than the prototype sketch. All of
 * these extended classes have backing CSS in
 * `frontend/src/design-system/components.css` (`.map-tip__kpi*` and
 * `.map-tip__seg*`) and use design tokens only — no class was invented
 * without a matching rule. Do NOT rename back to the prototype's flatter
 * vocabulary; that would drop the KPI grid styling.
 */

interface USChoroplethMapTooltipProps {
  hover: HoverState;
  activeSegNames: Set<string> | null;
}

export function USChoroplethMapTooltip({ hover, activeSegNames }: USChoroplethMapTooltipProps) {
  return createPortal(
    <div
      className="map-tip"
      style={{
        position: 'fixed',
        left: Math.max(160, Math.min(window.innerWidth - 160, hover.x)),
        top: hover.y - 4,
      }}
    >
      <div className="map-tip__name">{hover.name}</div>
      <div className="map-tip__kpis">
        <div className="map-tip__kpi">
          <div className="map-tip__kpi-label">Marketable borrowers</div>
          <div className="map-tip__kpi-value">
            {hover.count !== null ? hover.count.toLocaleString() : '—'}
          </div>
        </div>
        <div className="map-tip__kpi">
          <div className="map-tip__kpi-label">Avg. opportunity score</div>
          <div className="map-tip__kpi-value">
            {hover.avgScore !== null ? hover.avgScore : '—'}
          </div>
        </div>
      </div>
      {/* Addressable-vs-contactable reconciliation, stated on the tile
          before the click. The KPI above is the addressable population;
          the Lead Queue this tile links to applies the contact-eligibility
          predicate, so it shows a strict subset — live 2026-08-11, IL was
          76,711 of 1,851,040 (24x). Clicking a big number and landing on a
          small one reads as a broken link unless the tile says so. Same
          idiom as `.zip-tiles__reconcile` on the ZIP drill; reuses the
          existing muted compact row, no new CSS. */}
      {typeof hover.contactable === 'number' && hover.count !== null && (
        <div className="map-tip__row map-tip__row--compact map-tip__row--muted">
          <span>Contactable</span>
          <span className="v num map-tip__value--small">
            {hover.contactable.toLocaleString()} of {hover.count.toLocaleString()}
          </span>
        </div>
      )}
      {hover.topSegment && (
        <div className="map-tip__seg">
          <span className="map-tip__seg-label">Top segment</span>
          <span className="map-tip__seg-value">{hover.topSegment}</span>
        </div>
      )}
      {/* Drill-gap disclosure. The ZIP layer is keyed on a 5-digit ZIP and
          the share does not carry one for every property, so this state's
          ZIP tiles will sum BELOW the marketable count above. Disclosed on
          the state hover — before the drill — so the shortfall is expected
          rather than discovered. Reuses the existing muted compact row; no
          new CSS. */}
      {typeof hover.zipUnassigned === 'number' && hover.zipUnassigned > 0 && (
        <div className="map-tip__row map-tip__row--compact map-tip__row--muted">
          <span>ZIP coverage</span>
          <span className="v map-tip__value--small">
            {hover.zipUnassigned.toLocaleString()} borrowers without ZIP assignment
          </span>
        </div>
      )}
      {hover.overlay && (
        <div className="map-tip__overlay">
          <div className="map-tip__row map-tip__row--compact">
            <span>Leads</span>
            <span className="v num map-tip__value--small">
              {hover.overlay.leadCount !== null ? hover.overlay.leadCount.toLocaleString() : '—'}
            </span>
          </div>
          <div className="map-tip__row map-tip__row--compact">
            <span>Assigned</span>
            <span className="v num map-tip__value--small">
              {hover.overlay.assignedCount !== null ? hover.overlay.assignedCount.toLocaleString() : '—'}
            </span>
          </div>
          <div className="map-tip__row map-tip__row--compact">
            <span>Unattended</span>
            <span className="v num map-tip__value--small">
              {hover.overlay.unattendedCount !== null ? hover.overlay.unattendedCount.toLocaleString() : '—'}
            </span>
          </div>
          <div className="map-tip__row map-tip__row--compact">
            <span>LO coverage</span>
            <span className="v num map-tip__value--small">
              {hover.overlay.coveringOfficerCount !== null
                ? hover.overlay.coveringOfficerCount.toLocaleString()
                : '—'}
            </span>
          </div>
          {hover.overlay.coveringOfficers && hover.overlay.coveringOfficers.length > 0 && (
            <div className="map-tip__row map-tip__row--compact map-tip__row--muted">
              <span className="v map-tip__value--small">
                {hover.overlay.coveringOfficers.join(', ')}
              </span>
            </div>
          )}
        </div>
      )}
      {activeSegNames !== null && (
        <div className="map-tip__row map-tip__row--compact map-tip__row--muted">
          <span>Filter</span>
          <span className="v map-tip__value--small">
            filtered by {Array.from(activeSegNames).join(', ')}
          </span>
        </div>
      )}
      <div className="map-tip__row map-tip__row--compact">
        <span>Source</span>
        <span className="v mono map-tip__value--small">
          {hover.sourceHint ?? 'mip.gold'}
        </span>
      </div>
    </div>,
    document.body,
  );
}
