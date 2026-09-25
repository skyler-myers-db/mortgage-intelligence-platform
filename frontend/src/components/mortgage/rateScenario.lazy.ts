/**
 * The Rate Lever control's code-split entry (audit wow-stage-1). The legend
 * renders the lazy component; the map header warms the same chunk on
 * pointer-enter or focus of "Rate scenario" (JS only: no API call is made
 * until the mode is actually picked).
 */
import { lazy } from 'react';

export const loadRateScenarioControl = () => import('./RateScenarioControl');

export const RateScenarioControlLazy = lazy(loadRateScenarioControl);
