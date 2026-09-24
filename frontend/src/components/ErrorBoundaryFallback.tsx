import type { ClientErrorKind } from '../lib/chunkLoadError';
import { resolveRouteMeta } from '../lib/routeMeta';
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

/**
 * Product name for a pathname, read from the route registry (lib/routeMeta,
 * audit 2026-09-21 `shell-08`) so the error surface names a page exactly as
 * its tab title, route announcer and command palette do. Returns the route NAME,
 * never the path: a `/borrower-360/B-...` pathname carries a borrower id.
 * Null for a path the app does not serve. The registry is a pure module (no
 * router context), so the root boundary can still call this outside every
 * provider.
 */
export function routeLabelForPath(pathname: string): string | null {
  const clean = pathname.split(/[?#]/, 1)[0] || '/';
  const meta = resolveRouteMeta(clean);
  return meta.id === 'notFound' ? null : meta.name;
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
          <h1 className="h-3">{title}</h1>
          <p className="body error-surface__sub">{sub}</p>
          {routeLabel && (
            <p className="mono muted error-surface__meta">Route · {routeLabel}</p>
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
