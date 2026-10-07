/**
 * The ZCTA polygon rung's code-split entry (audit dataviz-01 / visual-09,
 * D-dataviz-geo-e; deviation:zcta-level). The map loads it with
 * useLazyModule the first time a Segment Intelligence drill shows the ZIP
 * level as a map, and the national stage warms the same chunk on pointer
 * entry (JS only: no geometry is fetched until a drill mounts the rung).
 *
 * Not React.lazy: a rejected import (a chunk a redeploy retired, a network
 * blip) must degrade the rung alone, to the densest-ZIP tiles with a status
 * line, never throw into the route error boundary (the RATE_SCENARIO_CONTROL
 * pattern, see useLazyModule).
 */
import { lazyModule } from './useLazyModule';

export const ZCTA_LEVEL = lazyModule(() => import('./USChoroplethMapZctaLevel'));
