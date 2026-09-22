import { useId, type ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import { Icon } from '../Icon';
import { Chip } from '../Primitives';

/**
 * AccessDenied — the in-app 403 surface (2026-09-21 audit critic-03, shell-06).
 *
 * Backend AdminDep / ApproverDep checks stay the security boundary; this is
 * only what a denied actor sees. It replaces two dead ends: the asset-detail
 * 403 that offered nothing but "Return home", and the admin deep link that
 * bounced to Home without a word. It always says which role the surface
 * needs and always offers a way back.
 *
 * Composition is existing prototype BEM only (`.surface`, `.surface__hdr`,
 * `.surface__body`, `.chip--warning`, `.btn`); no new block.
 *
 * "Back" is offered only when there is in-app history to go back to. React
 * Router keys the first entry of a session `default`, so a cold deep link or
 * a new tab gets Home as its single, primary way out instead of a Back button
 * that would leave the product.
 */
interface AccessDeniedProps {
  /** Short heading, e.g. "Administrator access required". */
  title: string;
  /** Human name of the role the surface requires, e.g. "Administrator". */
  requiredRole: string;
  /** Why the surface is restricted, in one or two sentences. */
  children: ReactNode;
  /**
   * `denied`: the server said this actor lacks the role. `unverified`: the
   * role check itself did not complete, so the surface stays closed
   * (fail-closed) without claiming the actor lacks a role nobody confirmed.
   */
  status?: 'denied' | 'unverified';
  /** Offered for `unverified`: re-run the check that did not complete. */
  onRetry?: () => void;
  testId?: string;
}

export function AccessDenied({
  title,
  requiredRole,
  children,
  status = 'denied',
  onRetry,
  testId,
}: AccessDeniedProps) {
  const headingId = useId();
  const navigate = useNavigate();
  const location = useLocation();
  const canGoBack = location.key !== 'default';
  const unverified = status === 'unverified';
  const canRetry = unverified && onRetry !== undefined;

  return (
    <section className="surface" aria-labelledby={headingId} data-testid={testId}>
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <Icon name="shield" size={14} className="icon-accent" />
          <h2 className="h-4" id={headingId}>{title}</h2>
        </div>
        <Chip variant="warning" icon="info">
          {unverified ? 'Access not verified' : '403 · Access denied'}
        </Chip>
      </div>
      <div className="surface__body">
        <p className="body flush">{children}</p>
        <p className="muted fs-12" data-testid="access-denied-role">
          Required role: <strong>{requiredRole}</strong>.{' '}
          {unverified
            ? 'Your session could not be checked, so this view stays closed and nothing restricted was loaded.'
            : `Your session does not have it, so nothing restricted was loaded. Ask your workspace administrator to grant the ${requiredRole} role if you need this view.`}
        </p>
        <div className="chip-row mt-3">
          {canRetry && (
            <button type="button" className="btn btn--primary btn--sm" onClick={onRetry}>
              Check access again
            </button>
          )}
          {canGoBack && (
            <button
              type="button"
              className={`btn btn--sm ${canRetry ? 'btn--ghost' : 'btn--primary'}`}
              onClick={() => navigate(-1)}
            >
              Back to previous page
            </button>
          )}
          <Link className={`btn btn--sm ${canGoBack || canRetry ? 'btn--ghost' : 'btn--primary'}`} to="/">
            <Icon name="home" size={12} />
            Go to Home
          </Link>
        </div>
      </div>
    </section>
  );
}
