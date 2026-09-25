import { lazy, Suspense, type ComponentType } from 'react';

/**
 * DescribedErrorBody — a failure's body sentence in the shared, buyer-safe
 * vocabulary (lib/describeApiError, audit 2026-09-21 `states-04`), e.g. "The
 * server hit an unexpected error.", without putting that vocabulary in a
 * route's natural-load closure: it arrives with AsyncStatus's failure chunk
 * (AsyncFailure.tsx), the first time a failure is on screen. Until then, or
 * if that chunk cannot load, the line reads "It could not load." (buyer-safe
 * too).
 *
 * React.lazy, not useLazyModule: lazy and Suspense ship with React, so this
 * wrapper is all a route's closure carries. A chunk that cannot load (or a
 * vite:preloadError handler that resolves it to undefined) renders the
 * fallback sentence, never a throw into the route's error boundary.
 *
 * It loads AsyncFailure, not lib/describeApiError itself: a second dynamic
 * entry reaching the transport made the bundler split lib/apiTransport out of
 * the entry chunk too (+0.45 KiB br of initial JS, measured).
 */
export interface DescribedErrorProps {
  error: unknown;
  /** What failed, lower case mid-sentence: "the property lookup". */
  subject: string;
}

export const DESCRIBED_ERROR_FALLBACK = 'It could not load.';

type FailureModule = typeof import('./AsyncFailure');

const loadFailure = () => import('./AsyncFailure') as Promise<FailureModule | undefined>;

/** Load the vocabulary ahead of need (a test); the first render still resolves the lazy wrapper. */
export const preloadDescribedError = (): Promise<unknown> => loadFailure();

const Fallback = () => DESCRIBED_ERROR_FALLBACK;

const Body = lazy<ComponentType<DescribedErrorProps>>(() => loadFailure().then(
  (module) => ({ default: module?.FailureBody ?? Fallback }),
  () => ({ default: Fallback }),
));

export function DescribedErrorBody(props: DescribedErrorProps) {
  return (
    <Suspense fallback={DESCRIBED_ERROR_FALLBACK}>
      <Body {...props} />
    </Suspense>
  );
}
