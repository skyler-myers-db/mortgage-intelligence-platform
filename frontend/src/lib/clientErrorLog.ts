import type { RootOptions } from 'react-dom/client';
import { classifyClientError, type ClientErrorKind } from './chunkLoadError';
import { resolveRouteMeta } from './routeMeta';
import {
  CLIENT_ERROR_BOUNDARIES,
  CLIENT_ERROR_NAMES,
  queueClientError,
  type ClientErrorBoundary,
  type ClientErrorName,
  type ClientErrorSource,
} from './rumBridge';

/**
 * The one place client-side exceptions are recorded.
 *
 * React 19's `createRoot` error callbacks, the error boundaries, the
 * stale-chunk listener and the window `error` / `unhandledrejection`
 * listeners all funnel through `reportClientError`. It writes the raw error
 * to this browser's console for whoever is debugging locally, and queues a
 * telemetry-safe report in lib/rumBridge. That report is the ONLY thing that
 * can ever leave the browser (through lib/rum, when RUM is on), under two
 * hard rules the `ClientErrorReport` shape enforces:
 *
 *   - never send `error.message`, a stack or the component stack: each can
 *     carry borrower ids, query strings and free text. The error NAME is
 *     mapped onto a closed list (anything else is `Other`);
 *   - never send a concrete pathname: `/borrower-360/B-...` carries a
 *     borrower id. Send the route registry's pattern (`/borrower-360/:id`,
 *     `/*` for a path the app does not serve).
 *
 * The backend RUM schema is closed to the same vocabularies
 * (backend/schemas/telemetry.py), so a message could not pass it anyway.
 */

export type { ClientErrorSource } from './rumBridge';

/** Telemetry-safe description of a client error. Deliberately message-free. */
export interface ClientErrorReport {
  source: ClientErrorSource;
  kind: ClientErrorKind;
  /** `Error.name` when it is on the closed list, otherwise `Other`. */
  errorName: ClientErrorName;
  /** The product ErrorBoundary that caught it, when there is one. */
  boundary: ClientErrorBoundary | null;
  /** Route registry pattern of the page it happened on. */
  route: string;
}

export interface ClientErrorContext {
  boundary?: string | null;
  componentStack?: string | null;
}

const KNOWN_NAMES: ReadonlySet<string> = new Set(CLIENT_ERROR_NAMES);
const KNOWN_BOUNDARIES: ReadonlySet<string> = new Set(CLIENT_ERROR_BOUNDARIES);

function rawErrorName(error: unknown): string | null {
  if (typeof error !== 'object' || error === null) return null;
  const { name } = error as { name?: unknown };
  return typeof name === 'string' ? name : null;
}

function errorNameOf(error: unknown): ClientErrorName {
  const name = rawErrorName(error);
  return name !== null && KNOWN_NAMES.has(name) ? (name as ClientErrorName) : 'Other';
}

function productBoundary(boundary: string | null | undefined): ClientErrorBoundary | null {
  return typeof boundary === 'string' && KNOWN_BOUNDARIES.has(boundary)
    ? (boundary as ClientErrorBoundary)
    : null;
}

function boundaryIdOf(instance: unknown): string | null {
  if (typeof instance !== 'object' || instance === null) return null;
  const { props } = instance as { props?: unknown };
  if (typeof props !== 'object' || props === null) return null;
  const { boundary } = props as { boundary?: unknown };
  return typeof boundary === 'string' ? boundary : null;
}

function currentRouteTemplate(): string {
  if (typeof window === 'undefined') return '/*';
  return resolveRouteMeta(window.location.pathname).pattern;
}

/**
 * What is queued for telemetry. An aborted request is not an error, and a
 * `caught` report without a product boundary is React Router's internal
 * boundary, which RethrowToRootBoundary re-throws: the root catch reports it.
 */
function isTelemetryWorthy(report: ClientErrorReport, error: unknown): boolean {
  if (rawErrorName(error) === 'AbortError') return false;
  return !(report.source === 'caught' && report.boundary === null);
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
    boundary: productBoundary(context.boundary),
    route: currentRouteTemplate(),
  };
  // The raw error and component stack stay in this browser's console; only
  // `report` is ever telemetry-safe. The browser already printed window and
  // rejection errors itself, so a second line would only be noise.
  if (source !== 'window' && source !== 'rejection') {
    console.error('[mip] client error', report, error, context.componentStack ?? '');
  }
  if (isTelemetryWorthy(report, error)) queueClientError(report);
  return report;
}

/**
 * Errors React never sees: a throw in an event handler, a timer or a
 * rejected promise nobody awaited. Bubble phase on purpose: a failed
 * `<img>` / `<script>` load dispatches a non-bubbling `error` that only a
 * capture listener would see, and those are not script errors. main.tsx
 * installs these before `createRoot`. Returns the uninstall function.
 */
export function installClientErrorListeners(): () => void {
  const onError = (event: ErrorEvent) => {
    reportClientError('window', event.error ?? null);
  };
  const onRejection = (event: PromiseRejectionEvent) => {
    reportClientError('rejection', event.reason);
  };
  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onRejection);
  return () => {
    window.removeEventListener('error', onError);
    window.removeEventListener('unhandledrejection', onRejection);
  };
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
