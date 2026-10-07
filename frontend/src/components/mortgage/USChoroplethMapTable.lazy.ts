/**
 * The geography map's "View as table" code-split entry: the table (with its
 * rate-mode change columns and sorting, wow-stage-1) loads the first time a
 * user picks the table view, so neither hero route carries it until then.
 * The rows themselves (USChoroplethMap.table) stay in the map chunk: the
 * legend's "not drawn on the map" note reads them in every view.
 *
 * Loaded with useLazyModule, not React.lazy: a chunk a redeploy retired
 * degrades the table view alone (a line with Reload), never the route.
 */
import { lazyModule } from './useLazyModule';

export const MAP_TABLE = lazyModule(() => import('./USChoroplethMapTable'));
