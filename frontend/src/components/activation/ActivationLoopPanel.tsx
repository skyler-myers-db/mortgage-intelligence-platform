import { useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import { formatTimestamp } from '../../lib/time';
import type {
  ActivationDestination,
  ActivationDestinationStatus,
  ActivationOutboxItem,
} from '../../types';
import { Button, Chip } from '../Primitives';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';

type Channel = 'email' | 'sms' | 'direct_mail';

function statusVariant(status: ActivationDestinationStatus | ActivationOutboxItem['status']): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'connected' || status === 'staged' || status === 'delivered') return 'success';
  if (status === 'disabled' || status === 'failed' || status === 'cancelled') return 'danger';
  if (status === 'dry_run' || status === 'not_configured') return 'warning';
  return 'neutral';
}

function statusDot(status: ActivationDestinationStatus | ActivationOutboxItem['status']): 'ok' | 'warn' | 'error' | 'neutral' {
  if (status === 'connected' || status === 'staged' || status === 'delivered') return 'ok';
  if (status === 'disabled' || status === 'failed' || status === 'cancelled') return 'error';
  if (status === 'dry_run' || status === 'not_configured') return 'warn';
  return 'neutral';
}

function statusLabel(status: string): string {
  return status.replace(/_/g, ' ');
}

function formatWhen(value?: string | null): string {
  if (!value) return 'No timestamp';
  // lib/time parses naive-UTC wire strings correctly and attaches an
  // explicit timezone name (2026-06-11 audit fix).
  return formatTimestamp(value);
}

export function ActivationLoopPanel({
  borrowerId,
  offerCode,
  channel = 'email',
  approvalId,
  approved = false,
}: {
  borrowerId?: string | null;
  offerCode?: string | null;
  channel?: Channel;
  approvalId: string | null;
  approved?: boolean;
}) {
  const queryClient = useQueryClient();
  const [selectedDestination, setSelectedDestination] = useState<string>('');
  const [stageError, setStageError] = useState<string | null>(null);
  const [stageResult, setStageResult] = useState<ActivationOutboxItem | null>(null);
  const [staging, setStaging] = useState(false);

  const {
    data: destinations,
    warmingUp: destinationsWarming,
    error: destinationsError,
  } = useWarmingUpRetry<ActivationDestination[]>(
    (signal) => api.activationDestinations(signal),
    [],
    { queryKey: queryKeys.activationDestinations() },
  );
  const {
    data: outbox,
    warmingUp: outboxWarming,
    error: outboxError,
  } = useWarmingUpRetry<ActivationOutboxItem[]>(
    (signal) => api.activationOutbox({ borrowerId, limit: 6 }, signal),
    [borrowerId],
    {
      enabled: Boolean(borrowerId),
      queryKey: queryKeys.activationOutbox(['borrower', borrowerId ?? '']),
    },
  );

  const stageableDestinations = useMemo(
    () => (destinations ?? []).filter((destination) => destination.allowed_actions.includes('stage_lead')),
    [destinations],
  );
  useEffect(() => {
    if (!selectedDestination && stageableDestinations.length > 0) {
      setSelectedDestination(stageableDestinations[0].destination_key);
    }
  }, [selectedDestination, stageableDestinations]);

  const selected = stageableDestinations.find((destination) => destination.destination_key === selectedDestination);
  const matchingActivation = (outbox ?? []).find((item) =>
    item.destination_key === selectedDestination
    && item.approval_id === approvalId
    && ['dry_run', 'staged', 'failed', 'delivered'].includes(item.status)
  ) ?? (
    stageResult && stageResult.destination_key === selectedDestination
      ? stageResult
      : null
  );
  const retryActivation = matchingActivation?.status === 'failed' ? matchingActivation : null;
  const existingActivation = retryActivation ? null : matchingActivation;
  const canStage = Boolean(
    borrowerId
    && approved
    && approvalId
    && selected
    && selected.status !== 'disabled'
    && !existingActivation
    && !staging,
  );

  const stage = async () => {
    if (!borrowerId || !selected || !approvalId) return;
    setStageError(null);
    setStageResult(null);
    setStaging(true);
    try {
      const result = await api.stageActivation({
        borrower_id: borrowerId,
        destination_key: selected.destination_key,
        offer_code: offerCode ?? null,
        channel,
        approval_id: approvalId,
      });
      setStageResult(result.activation);
      await queryClient.invalidateQueries({ queryKey: ['mip', 'activation'] });
    } catch (err) {
      setStageError(err instanceof Error ? err.message : 'Unable to stage activation.');
    } finally {
      setStaging(false);
    }
  };

  return (
    <div className="surface mt-grid">
      <div className="surface__hdr surface__hdr--split">
        <div>
          <div className="h-4">Customer activation loop</div>
          <div className="muted fs-12">
            Stage approved work for CRM, CDP, LOS/POS, or servicing writeback without auto-sending outreach.
          </div>
        </div>
        <Chip variant={approved ? 'success' : 'neutral'}>{approved ? 'approval ready' : 'approval required'}</Chip>
      </div>
      <div className="surface__body surface__body--stack-sm">
        {destinationsWarming && <WarmingUpBlock state={destinationsWarming} title="Activation destinations loading" compact />}
        {outboxWarming && <WarmingUpBlock state={outboxWarming} title="Activation outbox loading" compact />}
        {destinationsError && (
          <div className="muted body fs-12">Activation destinations unavailable: {destinationsError.message}</div>
        )}
        {!destinationsWarming && !destinationsError && stageableDestinations.length > 0 && (
          <div className="filter-row">
            <label className="filter-row__group">
              <span className="field__label">DESTINATION</span>
              <select
                value={selectedDestination}
                onChange={(event) => setSelectedDestination(event.target.value)}
                disabled={staging}
              >
                {stageableDestinations.map((destination) => (
                  <option key={destination.destination_key} value={destination.destination_key}>
                    {destination.display_name}
                  </option>
                ))}
              </select>
            </label>
            <div className="admin-filter-actions">
              {selected && (
                <Chip variant={statusVariant(selected.status)}>{statusLabel(selected.status)}</Chip>
              )}
              <Button
                variant="primary"
                size="sm"
                icon="send"
                onClick={() => void stage()}
                disabled={!canStage}
              >
                {existingActivation ? 'Staged' : staging ? 'Staging' : retryActivation ? 'Retry' : 'Stage'}
              </Button>
            </div>
          </div>
        )}
        {!destinationsWarming && !destinationsError && stageableDestinations.length === 0 && (
          <div className="muted body fs-12">No activation destinations are registered.</div>
        )}
        {!approved && (
          <div className="muted body fs-12">
            Approve the governed outreach decision before staging a customer-system handoff.
          </div>
        )}
        {approved && !approvalId && (
          <div className="muted body fs-12">
            Activation requires the durable approval ID from Lakebase before a customer-system handoff can be staged.
          </div>
        )}
        {selected && selected.status !== 'connected' && (
          <div className="muted body fs-12">
            This destination is not connected; staging writes a dry-run outbox row for implementation proof only.
          </div>
        )}
        {stageError && <div className="muted body fs-12 text-danger" role="alert">{stageError}</div>}
        {stageResult && (
          <div className="chip-row" role="status">
            <Chip variant={statusVariant(stageResult.status)}>{statusLabel(stageResult.status)}</Chip>
            <span className="mono muted fs-11">activation: {stageResult.activation_id}</span>
          </div>
        )}
        {outboxError && !outboxWarming && (
          <div className="muted body fs-12">Outbox unavailable: {outboxError.message}</div>
        )}
        {!outboxError && (outbox ?? []).map((item) => (
          <div key={item.activation_id} className="source-status-row">
            <span aria-hidden className={`status-dot status-dot--${statusDot(item.status)}`} />
            <div className="source-status-main">
              {item.destination_display_name}
              <span className="muted source-status-count">{item.destination_type}</span>
            </div>
            <div className="source-status-meta">
              <Chip variant={statusVariant(item.status)}>{statusLabel(item.status)}</Chip>
              <span className="muted fs-11">{formatWhen(item.created_at)}</span>
            </div>
          </div>
        ))}
        {!outboxError && borrowerId && outbox && outbox.length === 0 && (
          <div className="muted body fs-12">No activation rows have been staged for this borrower.</div>
        )}
      </div>
    </div>
  );
}

export function ActivationOperationsPanel() {
  const {
    data,
    warmingUp,
    error,
  } = useWarmingUpRetry(
    (signal) => api.activationSummary(signal),
    [],
    { queryKey: queryKeys.activationSummary() },
  );
  const connectedCount = data ? data.destinations.filter((destination) => destination.status === 'connected').length : 0;
  return (
    <div className="surface mt-grid">
      <div className="surface__hdr surface__hdr--split">
        <div>
          <div className="h-4">Activation destinations</div>
          <div className="muted fs-12">
            Governed outbox for CRM, CDP, LOS/POS, and servicing handoff. No destination secrets are stored in the browser.
          </div>
        </div>
        <Chip variant={connectedCount > 0 ? 'success' : 'warning'}>
          {error ? 'unavailable' : data ? `${connectedCount} connected` : 'loading'}
        </Chip>
      </div>
      <div className="surface__body surface__body--stack-sm">
        {warmingUp && <WarmingUpBlock state={warmingUp} title="Activation status loading" compact />}
        {error && !warmingUp && (
          <div className="muted body fs-12">Activation status unavailable: {error.message}</div>
        )}
        {data?.destinations.map((destination) => (
          <div key={destination.destination_key} className="source-status-row">
            <span aria-hidden className={`status-dot status-dot--${statusDot(destination.status)}`} />
            <div className="source-status-main">
              {destination.display_name}
              <span className="muted source-status-count">{destination.destination_type}</span>
              <div className="muted fs-12">{destination.allowed_actions.join(', ')}</div>
            </div>
            <div className="source-status-meta">
              <Chip variant={statusVariant(destination.status)}>{statusLabel(destination.status)}</Chip>
              <span className="muted fs-11">{formatWhen(destination.updated_at)}</span>
            </div>
          </div>
        ))}
        {data?.recent_outbox.length ? (
          <div className="admin-rollups">
            <div className="h-5">Recent staged rows</div>
            <div className="admin-rollups__grid">
              {data.recent_outbox.slice(0, 6).map((item) => (
                <div key={item.activation_id} className="admin-rollup">
                  <span className="mono fs-12">{item.destination_key}</span>
                  <strong>{statusLabel(item.status)}</strong>
                  <span className="muted fs-11">{item.borrower_id ?? item.entity_id}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
