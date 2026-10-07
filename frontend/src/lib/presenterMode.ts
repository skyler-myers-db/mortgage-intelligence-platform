import { useQuery } from '@tanstack/react-query';
import { sessionQueryOptions } from './sessionQuery';

/**
 * The demo-only presenter flag (D-shell-deviations-e1): gates demo
 * affordances, never an authorization input. False while the session is
 * pending or errored, so a customer never sees a demo surface by accident.
 *
 * Its own module, not lib/sessionQuery. The shell rail reads it in the
 * initial chunk (W5c: the M1-M4 roadmap slots show only in presenter mode,
 * D-shell-deviations-e2); the Offer and Administration route chunks read it
 * too.
 */
export function usePresenterMode(): boolean {
  const session = useQuery(sessionQueryOptions());
  return session.data?.presenter_mode === true;
}
