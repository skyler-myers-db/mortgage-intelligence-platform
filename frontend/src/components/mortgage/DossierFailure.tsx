import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { ApiError } from '../../lib/api';
import { describeApiError, type ApiErrorKind } from '../../lib/describeApiError';
import { PageShell } from '../layout/PageShell';
import { Button, Chip } from '../Primitives';
import { useOptionalHealth } from '../HealthProvider';
import { degradedDependency, friendlyDependencyName, isBanneredOutage } from '../healthRecovery';

/**
 * Borrower 360's page for a dossier read that failed (audit 2026-09-21
 * `states-04` slice 2 and `states-03` part a). Loaded on demand: only a failed
 * read renders it, so none of it rides the dossier's natural load. A 404 keeps
 * its copy. Under a banner that already names the outage the dossier waits
 * calmly (HealthProvider refetches it on recovery). Any other failure speaks
 * the shared vocabulary (lib/describeApiError), never the transport message,
 * and offers Retry only when retrying can help.
 */

/** The status chip beside a failed dossier (never "Backend unavailable" for a 403). */
function failureChipLabel(kind: ApiErrorKind): string {
  if (kind === 'forbidden' || kind === 'permission_denied') return 'Access required';
  if (kind === 'session_expired') return 'Session ended';
  if (kind === 'offline') return 'Offline';
  if (kind === 'rate_limited') return 'Paused';
  return 'Unavailable';
}

interface FailurePageProps {
  id: string;
  pager: ReactNode;
  title: string;
  lede: string;
  chip: { label: string; variant: 'warning' | 'neutral' | 'danger'; icon: 'search' | 'bolt' | 'cross' };
  onRetry: (() => void) | null;
  quietRetry?: boolean;
}

function FailurePage({ id, pager, title, lede, chip, onRetry, quietRetry }: FailurePageProps) {
  return (
    <PageShell eyebrow="Borrower 360" title={title} lede={lede}>
      {pager}
      <div className="surface">
        <div className="surface__body surface__body--inline">
          <Chip variant={chip.variant} icon={chip.icon}>
            {chip.label}
          </Chip>
          {onRetry && (
            <Button variant={quietRetry ? 'ghost' : undefined} onClick={onRetry} aria-label={`Retry loading borrower ${id}`}>
              Retry
            </Button>
          )}
          <Link className="btn" to="/lead-queue">
            Back to lead queue
          </Link>
        </div>
      </div>
    </PageShell>
  );
}

export function DossierFailure({ id, error, pager, onRetry }: { id: string; error: Error; pager: ReactNode; onRetry: () => void }) {
  const healthCtx = useOptionalHealth();
  const health = healthCtx?.health ?? null;
  if (error instanceof ApiError && error.status === 404) {
    return (
      <FailurePage
        id={id}
        pager={pager}
        title={`Borrower ${id} not found`}
        lede={`Borrower ${id} was not found. Check the ID, use search, or return to the lead queue.`}
        chip={{ label: 'Not found', variant: 'warning', icon: 'search' }}
        onRetry={null}
      />
    );
  }
  if (isBanneredOutage(error, health, healthCtx?.connection ?? 'online')) {
    return (
      <FailurePage
        id={id}
        pager={pager}
        title={`Loading ${id}…`}
        lede={`This dossier reloads when the ${friendlyDependencyName(degradedDependency(health) ?? '')} reconnects.`}
        chip={{ label: 'Reconnecting', variant: 'neutral', icon: 'bolt' }}
        onRetry={onRetry}
        quietRetry
      />
    );
  }
  const failure = describeApiError(error, { subject: `borrower ${id}` });
  return (
    <FailurePage
      id={id}
      pager={pager}
      title={failure.title}
      lede={failure.body}
      chip={{ label: failureChipLabel(failure.kind), variant: 'danger', icon: 'cross' }}
      onRetry={failure.action === 'retry' ? onRetry : null}
    />
  );
}
