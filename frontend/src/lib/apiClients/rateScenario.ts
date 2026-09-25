/**
 * Rate Lever client (`GET /api/geo/rate-sensitivity`, audit wow-stage-1).
 *
 * Deliberately NOT spread into `api` (lib/api.ts spreads geoApi into the
 * initial closure): the geography map's live-facts hook imports this module
 * directly, so the client ships in the lazy map chunk. The read is an
 * aggregate over gold plus a live contactable count and writes no audit row;
 * it is still only issued after the user picks the rate colouring, never
 * prefetched.
 */
import type { RateSensitivityResponse } from '../../types/rateScenario';
import { getJson } from '../apiTransport';

export const RATE_SENSITIVITY_PATH = '/api/geo/rate-sensitivity';

export const rateScenarioApi = {
  rateSensitivity: (signal?: AbortSignal) => getJson<RateSensitivityResponse>(RATE_SENSITIVITY_PATH, signal),
};
