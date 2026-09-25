/**
 * The Rate Lever control's code-split entry (audit wow-stage-1). The map
 * loads it with useLazyModule the first time the rate colouring is on, and
 * the header warms the same chunk on pointer-enter or focus of "Rate
 * scenario" (JS only: no API call is made until the mode is actually picked).
 *
 * Not React.lazy: a rejected import (a chunk a redeploy retired, a network
 * blip) must degrade the lever alone, to a "could not load" line in the
 * legend over the borrower fill, never throw into the route error boundary
 * and take the hero map and the KPIs down with it (see useLazyModule).
 */
import { lazyModule } from './useLazyModule';

export const RATE_SCENARIO_CONTROL = lazyModule(() => import('./RateScenarioControl'));
