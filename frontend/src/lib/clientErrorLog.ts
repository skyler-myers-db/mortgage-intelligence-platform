import type { RootOptions } from 'react-dom/client';
import { classifyClientError, type ClientErrorKind } from './chunkLoadError';

/**
 * The one place client-side exceptions are recorded.
 *
 * React 19's `createRoot` error callbacks, the error boundaries and the
 * stale-chunk listener all funnel through `reportClientError`. Today it only
 * writes to the local console. A later wave adds sanitised telemetry HERE and
 * nowhere else, under two hard rules that the `ClientErrorReport` shape
 * already enforces:
 *
 *   - never send `error.message` or the component stack: both can carry
 *     borrower ids, query strings and free text;
 *   - never send a concrete pathname: `/borrower-360/B-...` carries a
 *     borrower id. Send the route template or nothing.
 *
 * The backend RUM schema is closed (`Literal` metric names, `extra=forbid`),
 * so that telemetry also needs a server-side allowlist change first. No
 * network call is made from this module.
 */

export type ClientErrorSource = 'uncaught' | 'caught' | 'recoverable' | 'preload';

/** Telemetry-safe description of a client error. Deliberately message-free. */
export interface ClientErrorReport {
  source: ClientErrorSource;
  kind: ClientErrorKind;
  /** `Error.name` only, e.g. `TypeError`. */
  errorName: string;
  /** `boundary` prop of the ErrorBoundary that caught it, when there is one. */
  boundary: string | null;
}

export interface ClientErrorContext {
  boundary?: string | null;
  componentStack?: string | null;
}

function errorNameOf(error: unknown): string {
  if (typeof error === 'object' && error !== null) {
    const { name } = error as { name?: unknown };
    if (typeof name === 'string' && name) return name;
  }
  return typeof error;
}

function boundaryIdOf(instance: unknown): string | null {
  if (typeof instance !== 'object' || instance === null) return null;
  const { props } = instance as { props?: unknown };
  if (typeof props !== 'object' || props === null) return null;
  const { boundary } = props as { boundary?: unknown };
  return typeof boundary === 'string' ? boundary : null;
}

export function reportClientError(
  source: ClientErrorSource,
  error: unknown,
  context: ClientErrorContext = {},
): ClientErrorReport {
  const report: ClientErrorReport = {
    source,
    kind: classifyClientError(error),
    errorName: errorNameOf(error),
    boundary: context.boundary ?? null,
  };
  // The raw error and component stack stay in this browser's console for
  // whoever is debugging locally; only `report` is ever telemetry-safe.
  console.error('[mip] client error', report, error, context.componentStack ?? '');
  return report;
}

type RootErrorOptions = Pick<
  RootOptions,
  'onUncaughtError' | 'onCaughtError' | 'onRecoverableError'
>;

/** `createRoot` options that route every React-reported error through `reportClientError`. */
export function rootErrorOptions(): RootErrorOptions {
  return {
    onUncaughtError: (error, info) => {
      reportClientError('uncaught', error, { componentStack: info.componentStack });
    },
    onCaughtError: (error, info) => {
      reportClientError('caught', error, {
        boundary: boundaryIdOf(info.errorBoundary),
        componentStack: info.componentStack,
      });
    },
    onRecoverableError: (error, info) => {
      reportClientError('recoverable', error, { componentStack: info.componentStack });
    },
  };
}
