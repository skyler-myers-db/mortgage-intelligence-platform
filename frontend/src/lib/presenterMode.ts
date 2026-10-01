import { useQuery } from '@tanstack/react-query';
import { sessionQueryOptions } from './sessionQuery';

/**
 * The demo-only presenter flag (D-shell-deviations-e1): gates demo
 * affordances, never an authorization input. False while the session is
 * pending or errored, so a customer never sees a demo surface by accident.
 *
 * Its own module, not lib/sessionQuery: only lazy route chunks (Offer,
 * Administration) read it, so it stays out of the initial bundle the shell's
 * session read ships in.
 */
export function usePresenterMode(): boolean {
  const session = useQuery(sessionQueryOptions());
  return session.data?.presenter_mode === true;
}
