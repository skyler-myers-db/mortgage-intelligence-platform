import type { ClientErrorKind } from '../lib/chunkLoadError';
import { Icon } from './Icon';

/**
 * ErrorSurface — what an ErrorBoundary renders instead of a white page.
 *
 * Declared prototype extension: design_files has no error-page pattern, so
 * this composes the prototype's `.surface` / `.surface__body` / `.btn` with a
 * new `.error-surface` block (design-system/components/15-error-surface.css)
 * that mirrors the `.approval` / `.degraded-banner` icon-title-sub-actions
 * vocabulary.
 *
 * It never renders `error.message` or a stack, in any build: error text can
 * carry borrower ids and query strings. The raw error goes to the local
 * console through lib/clientErrorLog. It also takes no router / context
 * dependency, because the root boundary renders it outside every provider.
 */

export type ErrorSurfaceVariant = 'route' | 'page';

interface ErrorSurfaceProps {
  kind: ClientErrorKind;
  boundary: string;
  variant: ErrorSurfaceVariant;
  routeLabel: string | null;
  onRetry: () => void;
  onReload: () => void;
}

const ROUTE_LABELS: ReadonlyArray<readonly [prefix: string, label: string]> = [
  ['/analytics', 'Analytics'],
  ['/data-estate/assets', 'Governed asset'],
  ['/portfolio-builder', 'Portfolio Builder'],
  ['/segment-intelligence', 'Segments'],
  ['/lead-queue', 'Lead Queue'],
  ['/borrower-360', 'Borrower 360'],
  ['/glossary', 'Glossary'],
  ['/offer-orchestrator', 'Offer & Outreach'],
  ['/ask-genie', 'Ask Genie'],
  ['/admin-config', 'Administration'],
];

/**
 * Product name for a pathname. Returns the route NAME, never the path: a
 * `/borrower-360/B-...` pathname carries a borrower id.
 */
export function routeLabelForPath(pathname: string): string | null {
  const clean = pathname.split(/[?#]/, 1)[0] || '/';
  if (clean === '/') return 'Home';
  const match = ROUTE_LABELS.find(
    ([prefix]) => clean === prefix || clean.startsWith(`${prefix}/`),
  );
  return match ? match[1] : null;
}

function copyFor(
  kind: ClientErrorKind,
  variant: ErrorSurfaceVariant,
  routeLabel: string | null,
) {
  const where = routeLabel ?? 'This page';
  if (kind === 'chunk') {
    return {
      title: 'A new version is available',
      sub: `${where} could not finish loading, usually because the app was updated while this tab was open. Reload to pick up the latest version.`,
    };
  }
  if (variant === 'page') {
    return {
      title: 'The workspace hit an unexpected error',
      sub: 'The workspace could not be displayed. Try again, or reload the page if it keeps happening.',
    };
  }
  return {
    title: `${where} hit an unexpected error`,
    sub: 'The rest of the workspace is still available. Try again, or reload the page if it keeps happening.',
  };
}

export function ErrorSurface({
  kind,
  boundary,
  variant,
  routeLabel,
  onRetry,
  onReload,
}: ErrorSurfaceProps) {
  const { title, sub } = copyFor(kind, variant, routeLabel);
  return (
    <section
      className={`surface error-surface error-surface--${variant}`}
      role="alert"
      data-testid="error-surface"
      data-error-kind={kind}
      data-error-boundary={boundary}
    >
      <div className="surface__body error-surface__body">
        <div className="error-surface__ico" aria-hidden="true">
          <Icon name={kind === 'chunk' ? 'bolt' : 'info'} size={16} />
        </div>
        <div className="error-surface__copy">
          <h1 className="error-surface__title">{title}</h1>
          <p className="error-surface__sub">{sub}</p>
          {routeLabel && (
            <p className="error-surface__meta mono">Route · {routeLabel}</p>
          )}
        </div>
        <div className="error-surface__actions">
          {kind === 'render' && (
            <button type="button" className="btn btn--primary" onClick={onRetry}>
              Try again
            </button>
          )}
          <button
            type="button"
            className={`btn ${kind === 'chunk' ? 'btn--primary' : 'btn--ghost'}`}
            onClick={onReload}
          >
            Reload
          </button>
        </div>
      </div>
    </section>
  );
}
