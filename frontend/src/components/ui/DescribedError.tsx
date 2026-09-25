import type { ReactNode } from 'react';
import type { ApiErrorDescription } from '../../lib/describeApiError';
import { lazyModule, useLazyModule } from '../mortgage/useLazyModule';

/**
 * DescribedError — render a failure in the shared, buyer-safe vocabulary
 * (lib/describeApiError, audit 2026-09-21 `states-04`) without putting that
 * vocabulary in a route's natural-load closure: it arrives with AsyncStatus's
 * failure chunk (AsyncFailure.tsx re-exports it), the first time a failure is
 * on screen. Until then (or if the chunk cannot load) the `fallback` renders,
 * which must itself be buyer-safe copy.
 */
const FAILURE = lazyModule(() => import('./AsyncFailure'));

/** Load the vocabulary ahead of need (a server render, a test); later mounts render it at once. */
export const preloadDescribedError = (): Promise<unknown> => FAILURE.load();

interface DescribedErrorProps {
  error: unknown;
  /** What failed, sentence case: "Sales operations metrics". */
  subject: string;
  fallback?: ReactNode;
  children: (description: ApiErrorDescription) => ReactNode;
}

export function DescribedError({ error, subject, fallback = null, children }: DescribedErrorProps) {
  const failure = useLazyModule(FAILURE, true).module;
  if (!failure) return <>{fallback}</>;
  return <>{children(failure.describeApiError(error, { subject }))}</>;
}

/** Just the description's body sentence ("The server hit an unexpected error."). */
export function DescribedErrorBody({ error, subject }: { error: unknown; subject: string }) {
  return (
    <DescribedError error={error} subject={subject} fallback="It could not load.">
      {(failure) => failure.body}
    </DescribedError>
  );
}
