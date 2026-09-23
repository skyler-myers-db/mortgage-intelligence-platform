/**
 * The geography map's selection and its URL form (audit dataviz-04).
 *
 * The map is controlled: the route owns the selection in the URL as
 * `?geo_state=TX&zip=77002`, so "Clear geography", Back / Forward and a
 * shared link all show the same drill. Same pattern as the Analytics tab
 * (`?view=`, `?states=`): the search params are the state, parsed on read.
 */
import { USCODE_TO_FIPS } from './USChoroplethMap.utils';

/** Selection payload emitted on every state / ZIP click. State is a 2-char
 *  uppercase USPS code so consumers can run predicates against
 *  `LeadSummary.state` directly; `zip` is the 5-digit string. All nulls means
 *  the national view.
 *
 *  `county` is always null since 2026-08-08 — the map has no county level.
 *  The field stays on the contract (consumers still forward it to
 *  `/api/leads`, which still accepts a county predicate) so a licensed
 *  county dataset needs no consumer change. */
export interface MapSelection {
  state: string | null;
  county: string | null;
  zip: string | null;
}

export interface MapSelectionChangeOptions {
  /** Replace the current history entry instead of pushing a new one. */
  replace?: boolean;
}

export const EMPTY_MAP_SELECTION: MapSelection = Object.freeze({ state: null, county: null, zip: null });

/** Search param carrying the drilled state (`geo_state`, not `state`: the Lead Queue owns `state`). */
export const MAP_STATE_PARAM = 'geo_state';
export const MAP_ZIP_PARAM = 'zip';

/**
 * Parse the raw param values. Only an intrinsic USPS code drills (a typo or a
 * hostile value is the national view), and a ZIP is only meaningful inside a
 * drilled state.
 */
export function parseMapSelection(rawState: string | null, rawZip: string | null): MapSelection {
  const code = (rawState ?? '').trim().toUpperCase();
  const state = /^[A-Z]{2}$/.test(code) && USCODE_TO_FIPS[code.toLowerCase()] ? code : null;
  const zipText = (rawZip ?? '').trim();
  const zip = state && /^\d{5}$/.test(zipText) ? zipText : null;
  return { state, county: null, zip };
}

export function mapSelectionFromSearch(params: URLSearchParams): MapSelection {
  return parseMapSelection(params.get(MAP_STATE_PARAM), params.get(MAP_ZIP_PARAM));
}

/** A copy of `params` carrying `selection`; every other param is kept. */
export function withMapSelection(params: URLSearchParams, selection: MapSelection): URLSearchParams {
  const next = new URLSearchParams(params);
  const { state, zip } = parseMapSelection(selection.state, selection.zip);
  if (state) next.set(MAP_STATE_PARAM, state);
  else next.delete(MAP_STATE_PARAM);
  if (zip) next.set(MAP_ZIP_PARAM, zip);
  else next.delete(MAP_ZIP_PARAM);
  return next;
}

export function sameMapSelection(a: MapSelection, b: MapSelection): boolean {
  return a.state === b.state && a.county === b.county && a.zip === b.zip;
}
