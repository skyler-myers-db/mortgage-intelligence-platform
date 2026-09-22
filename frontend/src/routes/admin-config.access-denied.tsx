import { useQueryClient } from '@tanstack/react-query';
import { PageShell } from '../components/layout/PageShell';
import { AccessDenied } from '../components/ui/AccessDenied';

/**
 * What a non-admin sees on an /admin-config deep link (2026-09-21 audit
 * shell-06). AdminRouteGate used to `<Navigate to="/" replace />` with no
 * message: a bookmarked or shared admin link silently dumped the actor on
 * Home, indistinguishable from a broken link.
 *
 * Loaded lazily from app.tsx so the denied page costs the initial bundle
 * nothing. It renders no admin data and issues no admin read: the gate decides
 * from the server-authoritative session before this mounts.
 */
interface AdminAccessDeniedRouteProps {
  /** The session check failed rather than answering "no". */
  unverified?: boolean;
}

export default function AdminAccessDeniedRoute({ unverified = false }: AdminAccessDeniedRouteProps) {
  const queryClient = useQueryClient();

  return (
    <PageShell
      eyebrow="Administration"
      title={unverified ? 'Administrator access could not be confirmed' : 'Administrator access required'}
      lede="The offer ruleset, data source readiness, and the audit trail are an administrator surface."
    >
      <AccessDenied
        title={unverified ? 'Access check did not complete' : 'This page is limited to administrators'}
        requiredRole="Administrator"
        status={unverified ? 'unverified' : 'denied'}
        onRetry={() => {
          void queryClient.invalidateQueries({ queryKey: ['session', 'access'] });
        }}
        testId="admin-access-denied"
      >
        Admin Config shows the active offer ruleset, data source readiness, and the full audit
        trail. The lead workflow — portfolio, segments, lead queue, borrower dossiers, offers, and
        Genie — does not need it.
      </AccessDenied>
    </PageShell>
  );
}
