import { Component, type ReactNode } from 'react';
import { classifyClientError } from '../lib/chunkLoadError';
import { ErrorSurface, type ErrorSurfaceVariant } from './ErrorBoundaryFallback';

/**
 * ErrorBoundary — keeps a render throw or a failed lazy chunk from
 * unmounting the React root to a white page (2026-09-21 audit: stack-01,
 * shell-01, states-01, quality-01, bundle-01).
 *
 * Two are mounted: `boundary="root"` around the whole provider tree in
 * main.tsx, and `boundary="route"` around the route Suspense in app.tsx so a
 * broken route leaves the rail, topbar, Console and Genie usable.
 *
 * Recovery differs by failure kind:
 *   - chunk  : Reload only. React.lazy caches the rejected import, so
 *              re-rendering can never succeed.
 *   - render : "Try again" runs `onRetry`, then clears the boundary and
 *              re-renders the children; Reload is the fallback. The route
 *              boundary (ErrorBoundaryRoute) uses `onRetry` to discard the
 *              failed route's cached queries, so the re-mounted route re-reads
 *              its data instead of re-throwing on the same cached payload.
 *
 * Logging is NOT done here: React reports every boundary-caught error to the
 * root's `onCaughtError` (lib/clientErrorLog), which reads this boundary's
 * `boundary` prop, so one error produces one log entry.
 */

interface ErrorBoundaryProps {
  /** Stable id for the client error log, e.g. `root` or `route`. */
  boundary: string;
  /** A change clears a caught error. The route boundary passes the pathname. */
  resetKey?: string;
  /** Product name of the failed area, shown in the copy. Never a raw path. */
  routeLabel?: string | null;
  /** `page` centres the surface in the viewport (root boundary, no shell). */
  variant?: ErrorSurfaceVariant;
  /** Injected in tests; defaults to a full page reload. */
  onReload?: () => void;
  /** Runs on Try again, before the boundary clears and re-renders its children. */
  onRetry?: () => void;
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: unknown;
  hasError: boolean;
  resetKey: string | undefined;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = {
    error: null,
    hasError: false,
    resetKey: this.props.resetKey,
  };

  static getDerivedStateFromError(error: unknown): Partial<ErrorBoundaryState> {
    return { error, hasError: true };
  }

  static getDerivedStateFromProps(
    props: ErrorBoundaryProps,
    state: ErrorBoundaryState,
  ): Partial<ErrorBoundaryState> | null {
    if (props.resetKey === state.resetKey) return null;
    return { error: null, hasError: false, resetKey: props.resetKey };
  }

  private readonly retry = () => {
    this.props.onRetry?.();
    this.setState({ error: null, hasError: false });
  };

  private readonly reload = () => {
    if (this.props.onReload) this.props.onReload();
    else window.location.reload();
  };

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <ErrorSurface
        kind={classifyClientError(this.state.error)}
        boundary={this.props.boundary}
        variant={this.props.variant ?? 'route'}
        routeLabel={this.props.routeLabel ?? null}
        onRetry={this.retry}
        onReload={this.reload}
      />
    );
  }
}
