import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Chip } from '../Primitives';
import { Icon } from '../Icon';
import { api } from '../../lib/api';
import { DEFAULT_QUERY_STALE_MS } from '../../lib/queryClient';
import { queryKeys } from '../../lib/queryKeys';
import { formatTimestamp, parseBackendTimestamp } from '../../lib/time';

type OperationJobKey = 'fred_rates' | 'silver_refresh' | 'gold_refresh' | 'lifecycle_sync';

const ACTIVE_OPERATION_POLL_MS = 5_000;
const POST_LAUNCH_POLL_WINDOW_MS = 60_000;

interface OperationRun {
  run_id: number | null;
  life_cycle_state: string | null;
  result_state: string | null;
  state_message: string | null;
  started_at: string | null;
  ended_at: string | null;
  run_page_url: string | null;
  active: boolean;
}

interface OperationJobStatus {
  key: OperationJobKey;
  label: string;
  job_name: string;
  job_id: number | null;
  configured: boolean;
  description: string;
  run_order: number;
  cooldown_remaining_s?: number;
  latest_run: OperationRun | null;
  recent_runs?: OperationRun[];
}

interface OperationsResponse {
  jobs: OperationJobStatus[];
}

interface OperationLaunchResponse {
  accepted: boolean;
  key: OperationJobKey;
  label: string;
  job_name: string;
  job_id: number;
  run_id: number | null;
  run_page_url: string | null;
  audit_event_id: string | null;
}

interface SourceSummary {
  name: string;
  status: string;
  rows: number | null;
  last_updated: string | null;
  note: string;
}

interface DataOperationsPanelProps {
  sources?: SourceSummary[];
  sourcesLoading?: boolean;
  sourcesError?: boolean;
}

function newRequestId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = Array.from({ length: 16 }, () => Math.floor(Math.random() * 256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.map((byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function operationStatusTone(job: OperationJobStatus): 'ok' | 'warn' | 'error' | 'neutral' {
  if (!job.configured) return 'error';
  if (job.latest_run?.active) return 'warn';
  if (job.latest_run?.result_state === 'FAILED' || job.latest_run?.result_state === 'TIMEDOUT') return 'error';
  if (job.latest_run?.result_state === 'SUCCESS') return 'ok';
  return 'neutral';
}

function operationStatusLabel(job: OperationJobStatus): string {
  if (!job.configured) return 'not bound';
  if ((job.cooldown_remaining_s ?? 0) > 0) return `cooldown ${formatCooldown(job.cooldown_remaining_s ?? 0)}`;
  const run = job.latest_run;
  if (!run) return 'no runs';
  if (run.active) return run.life_cycle_state?.toLowerCase() ?? 'running';
  if (run.result_state) return run.result_state.toLowerCase();
  return run.life_cycle_state?.toLowerCase() ?? 'unknown';
}

function formatOperationRun(run: OperationRun | null): string {
  if (!run) return 'No run history';
  const stamp = run.ended_at ?? run.started_at;
  const when = stamp ? formatTimestamp(stamp) : 'timestamp unavailable';
  if (run.run_id) return `Run ${run.run_id} · ${when}`;
  return when;
}

function operationDuration(run: OperationRun | null): string {
  if (!run?.started_at || !run.ended_at) return 'Duration unavailable';
  const started = parseBackendTimestamp(run.started_at)?.getTime() ?? Number.NaN;
  const ended = parseBackendTimestamp(run.ended_at)?.getTime() ?? Number.NaN;
  if (!Number.isFinite(started) || !Number.isFinite(ended) || ended < started) {
    return 'Duration unavailable';
  }
  const seconds = Math.round((ended - started) / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function operationHistorySummary(job: OperationJobStatus): string {
  const runs = (job.recent_runs ?? []).filter((run) => run.run_id);
  return runs.length ? `${runs.length} recent runs` : '';
}

function formatCooldown(seconds: number): string {
  if (seconds <= 0) return '0m';
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}

// Timestamp rendering goes through lib/time (parses naive-UTC wire strings
// correctly and always attaches a timezone name) — 2026-06-11 audit fix.

function sourceFreshnessStats(sources: SourceSummary[] | undefined) {
  const rows = sources ?? [];
  const liveRows = rows.filter((source) => source.status === 'live' || source.status === 'demo_synthetic');
  const pendingRows = rows.filter((source) => ['roadmap', 'not_configured', 'configured_empty'].includes(source.status));
  const errorRows = rows.filter((source) => ['permission_denied', 'error'].includes(source.status));
  const dated = liveRows
    .map((source) => ({
      ...source,
      ts: parseBackendTimestamp(source.last_updated)?.getTime() ?? Number.NaN,
    }))
    .filter((source) => Number.isFinite(source.ts))
    .sort((a, b) => b.ts - a.ts);
  const missingTimestampRows = liveRows.filter((source) => (
    !Number.isFinite(parseBackendTimestamp(source.last_updated)?.getTime() ?? Number.NaN)
  ));
  const latest = dated[0] ?? null;
  const now = Date.now();
  const staleRows = dated.filter((source) => now - source.ts > 7 * 24 * 60 * 60 * 1000);
  return {
    liveCount: liveRows.length,
    syntheticCount: rows.filter((source) => source.status === 'demo_synthetic').length,
    pendingCount: pendingRows.length,
    errorCount: errorRows.length,
    staleCount: staleRows.length,
    missingTimestampCount: missingTimestampRows.length,
    latest,
  };
}

export function DataOperationsPanel({ sources, sourcesLoading = false, sourcesError = false }: DataOperationsPanelProps) {
  const queryClient = useQueryClient();
  const [operationRunningKey, setOperationRunningKey] = useState<OperationJobKey | null>(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [operationLaunch, setOperationLaunch] = useState<OperationLaunchResponse | null>(null);
  const [operationPolling, setOperationPolling] = useState(false);
  const [operationPollGeneration, setOperationPollGeneration] = useState(0);

  useEffect(() => {
    if (!operationPolling) return undefined;
    const timer = window.setTimeout(() => setOperationPolling(false), POST_LAUNCH_POLL_WINDOW_MS);
    return () => window.clearTimeout(timer);
  }, [operationPollGeneration, operationPolling]);

  const {
    data: operations,
    error: operationsErrorObj,
    isLoading: operationsLoading,
    refetch: retryOperations,
  } = useQuery<OperationsResponse, Error>({
    queryKey: queryKeys.adminOperations(),
    queryFn: ({ signal }) => api.adminOperations<OperationsResponse>(signal),
    staleTime: DEFAULT_QUERY_STALE_MS,
    retry: false,
    refetchInterval: (query) => {
      const data = query.state.data as OperationsResponse | undefined;
      const hasActiveRun = data?.jobs.some((job) => Boolean(job.latest_run?.active)) ?? false;
      return hasActiveRun || operationPolling ? ACTIVE_OPERATION_POLL_MS : false;
    },
  });
  const operationsError = operationsErrorObj
    ? operationsErrorObj instanceof Error
      ? operationsErrorObj.message
      : 'Operations endpoint unreachable'
    : null;
  const activeOperationCount = operations
    ? operations.jobs.filter((job) => job.latest_run?.active).length
    : 0;
  const freshnessStats = sourceFreshnessStats(sources);
  const freshnessAttentionCount = freshnessStats.pendingCount
    + freshnessStats.staleCount
    + freshnessStats.errorCount
    + freshnessStats.missingTimestampCount;
  const operationsChipVariant = operationsError || sourcesError || freshnessAttentionCount > 0
    ? 'warning'
    : activeOperationCount > 0 ? 'success' : 'neutral';
  const operationsChipLabel = operationsError
    ? 'unavailable'
    : sourcesError
      ? 'freshness unavailable'
      : operationsLoading
        ? 'loading...'
        : activeOperationCount > 0
          ? `${activeOperationCount} running`
          : freshnessAttentionCount > 0
            ? `${freshnessAttentionCount} attention`
            : 'ready';

  const runOperation = async (job: OperationJobStatus) => {
    if (!job.configured || operationRunningKey) return;
    setOperationError(null);
    setOperationLaunch(null);
    setOperationRunningKey(job.key);
    try {
      const launch = await api.adminRunOperation<OperationLaunchResponse>({
        job_key: job.key,
        confirm: true,
        reason: 'operator_refresh',
        request_id: newRequestId(),
      });
      setOperationLaunch(launch);
      setOperationPolling(true);
      setOperationPollGeneration((value) => value + 1);
      void retryOperations();
      void queryClient.invalidateQueries({
        queryKey: queryKeys.adminSources(),
      });
    } catch (err) {
      setOperationError(err instanceof Error ? err.message : 'Unable to start refresh job');
    } finally {
      setOperationRunningKey(null);
    }
  };

  return (
    <div
      id="data-operations"
      className="surface mt-grid"
      tabIndex={-1}
      aria-labelledby="data-operations-title"
    >
      <div className="surface__hdr surface__hdr--split">
        <div>
          <div className="h-4" id="data-operations-title">Data operations</div>
          <div className="muted fs-12">
            Governed refresh jobs for rates, source features, scoring snapshots, and workflow state.
          </div>
        </div>
        <Chip variant={operationsChipVariant}>{operationsChipLabel}</Chip>
      </div>
      <div className="surface__body surface__body--stack-sm">
        {operationsLoading && (
          <div className="muted body fs-12">Loading Databricks job status...</div>
        )}
        {operationsError && (
          <div className="muted body fs-12">Data operations unavailable: {operationsError}</div>
        )}
        {operationError && (
          <div className="muted body fs-12" role="status">
            {operationError}
          </div>
        )}
        {operationLaunch && (
          <div className="chip-row" role="status">
            <Chip variant="success">started</Chip>
            <span className="muted fs-12">
              {operationLaunch.label}
              {operationLaunch.run_id ? ` · run ${operationLaunch.run_id}` : ''}
            </span>
          </div>
        )}
        {!sourcesError && (
          <div className="admin-rollups" aria-label="Data freshness snapshot">
            <div className="admin-rollups__grid">
              <div className="admin-rollup">
                <span className="admin-rollup__label">Usable sources</span>
                <strong>{sourcesLoading ? '...' : freshnessStats.liveCount}</strong>
              </div>
              <div className="admin-rollup">
                <span className="admin-rollup__label">Demo synthetic</span>
                <strong>{sourcesLoading ? '...' : freshnessStats.syntheticCount}</strong>
              </div>
              <div className="admin-rollup">
                <span className="admin-rollup__label">Attention</span>
                <strong>{sourcesLoading ? '...' : freshnessAttentionCount}</strong>
              </div>
              <div className="admin-rollup">
                <span className="admin-rollup__label">Latest refresh</span>
                <strong>{sourcesLoading ? '...' : freshnessStats.latest?.last_updated ? formatTimestamp(freshnessStats.latest.last_updated) : 'No timestamp'}</strong>
              </div>
            </div>
          </div>
        )}
        {!operationsError && operations && (
          <div className="chip-row" aria-label="Recommended refresh order">
            {operations.jobs
              .slice()
              .sort((a, b) => a.run_order - b.run_order)
              .map((job) => (
                <Chip key={job.key} variant={job.latest_run?.active ? 'success' : 'neutral'}>
                  {job.run_order}. {job.label}
                </Chip>
            ))}
          </div>
        )}
        {!operationsError && operations?.jobs.map((job) => {
          const run = job.latest_run;
          const tone = operationStatusTone(job);
          const busy = operationRunningKey === job.key;
          const cooldown = job.cooldown_remaining_s ?? 0;
          const disabled = !job.configured || Boolean(operationRunningKey) || Boolean(run?.active) || cooldown > 0;
          return (
            <div key={job.key} className="source-status-row">
              <span aria-hidden className={`status-dot status-dot--${tone}`} />
              <div className="source-status-main">
                {job.label}
                <span className="muted source-status-count">{job.job_name}</span>
                <div className="muted fs-12">{job.description}</div>
              </div>
              <div className="source-status-meta">
                <div className="chip-row">
                  <Chip variant={tone === 'ok' ? 'success' : tone === 'error' ? 'warning' : 'neutral'}>
                    {operationStatusLabel(job)}
                  </Chip>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => void runOperation(job)}
                    disabled={disabled}
                    title={
                      !job.configured
                        ? 'Job binding unavailable'
                        : cooldown > 0
                          ? `Available in ${formatCooldown(cooldown)}`
                          : `Start ${job.label}`
                    }
                  >
                    <Icon name="play" size={12} />
                    {busy ? 'Starting' : cooldown > 0 ? 'Wait' : 'Run'}
                  </button>
                  {run?.run_page_url && (
                    <a
                      className="btn btn--ghost btn--sm"
                      href={run.run_page_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open run
                    </a>
                  )}
                </div>
                <span className="muted fs-11">
                  {formatOperationRun(run)} · {operationDuration(run)}
                </span>
                <span className="muted fs-11">
                  {operationHistorySummary(job)}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
