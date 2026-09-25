import { queryOptions } from '@tanstack/react-query';
import type { SessionResponse } from '../types';
import { api } from './api';

/**
 * The one `/api/session` read the shell observes (AppContext, RouteNav,
 * IdentityMenu and the admin route gate share its key). Zero-dependency on
 * the server, never retried: a failed check fails closed (no Admin tab, no
 * approve) instead of waiting out a retry plan. main.tsx seeds it from the
 * boot prime with these same options (lib/bootPrime).
 */
export function sessionQueryOptions() {
  return queryOptions<SessionResponse>({
    queryKey: ['session', 'access'],
    queryFn: ({ signal }) => api.session(signal),
    retry: false,
  });
}
