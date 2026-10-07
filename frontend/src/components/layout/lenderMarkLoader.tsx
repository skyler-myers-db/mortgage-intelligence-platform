import { lazy, type ComponentType } from 'react';
import { Icon } from '../Icon';
import type { LenderMarkProps } from './LenderMark';

/** The prototype's building glyph: what the tenant pill and chip draw whenever there is no mark to show. */
function TenantGlyph({ iconSize }: Pick<LenderMarkProps, 'iconSize'>) {
  return <Icon name="building" size={iconSize} />;
}

/**
 * The reviewed lender mark (responsive-10; deviation:lender-mark), loaded
 * lazily for the Topbar tenant pill and the Console tenant chip. Both render
 * it only when the build's mark meta is present, inside a Suspense whose
 * fallback is the glyph, so a default build never fetches its
 * chunk: not on first paint (W5b initial-JS cap) and not with the Console
 * (a static import made the Console chunk depend on the mark's).
 *
 * The mark is decorative, and the Topbar sits under the root boundary only.
 * A failed chunk (a network blip, or a stale chunk after a redeploy) resolves
 * to the glyph instead of throwing in render and replacing the whole shell
 * (or the Console panel) with an error surface. That includes the
 * stale-chunk listener's `vite:preloadError` preventDefault (taken whenever a
 * route load is in flight), which resolves the import to undefined. Vite's
 * preload wrapper takes the FIRST `.then` in with the import, so that
 * resolution skips it: the undefined guard has to sit in the second one.
 * Deliberately not lazyWithPreload: a decorative mark is never a
 * render-blocked load that may spend the guarded stale-chunk reload.
 */
export const LazyLenderMark = lazy<ComponentType<LenderMarkProps>>(() =>
  import('./LenderMark')
    .then((module) => module.LenderMark)
    .then(
      (mark: ComponentType<LenderMarkProps> | undefined) => ({ default: mark ?? TenantGlyph }),
      () => ({ default: TenantGlyph }),
    ),
);
