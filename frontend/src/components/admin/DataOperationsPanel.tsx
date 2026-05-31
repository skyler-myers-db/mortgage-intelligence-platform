import { useState } from 'react';
import { Chip } from '../Primitives';
import { Icon } from '../Icon';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';

type OperationJobKey = 'fred_rates' | 'silver_refresh' | 'gold_refresh' | 'lifecycle_sync';

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
  latest_run: OperationRun | null;
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

function newRequestId(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `ops-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
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

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`;
  } catch {
    return iso;
  }
}

export function DataOperationsPanel() {
  const [operationRunningKey, setOperationRunningKey] = useState<OperationJobKey | null>(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [operationLaunch, setOperationLaunch] = useState<OperationLaunchResponse | null>(null);

  const {
    data: operations,
    warmingUp: operationsWarming,
    error: operationsErrorObj,
    manualRetry: retryOperations,
  } = useWarmingUpRetry<OperationsResponse>(
    (signal) => api.adminOperations<OperationsResponse>(signal),
    [],
    { queryKey: queryKeys.adminOperations() },
  );
  const operationsLoading =
    operations === null && operationsWarming === null && operationsErrorObj === null;
  const operationsError = operationsErrorObj
    ? operationsErrorObj instanceof Error
      ? operationsErrorObj.message
      : 'Operations endpoint unreachable'
    : null;
  const activeOperationCount = operations
    ? operations.jobs.filter((job) => job.latest_run?.active).length
    : 0;

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
      retryOperations();
    } catch (err) {
      setOperationError(err instanceof Error ? err.message : 'Unable to start refresh job');
    } finally {
      setOperationRunningKey(null);
    }
  };

  return (
    <div className="surface mt-grid">
      <div className="surface__hdr surface__hdr--split">
        <div>
          <div className="h-4">Data operations</div>
          <div className="muted fs-12">
            Governed refresh jobs for rates, source features, scoring snapshots, and workflow state.
          </div>
        </div>
        <Chip variant={operationsError ? 'warning' : activeOperationCount > 0 ? 'success' : 'neutral'}>
          {operationsWarming
            ? 'warming up...'
            : operationsError
              ? 'unavailable'
              : operationsLoading
                ? 'loading...'
                : activeOperationCount > 0
                  ? `${activeOperationCount} running`
                  : 'ready'}
        </Chip>
      </div>
      <div className="surface__body surface__body--stack-sm">
        {operationsWarming && (
          <WarmingUpBlock state={operationsWarming} title="Operations loading" compact />
        )}
        {!operationsWarming && operationsLoading && (
          <div className="muted body fs-12">Loading Databricks job status...</div>
        )}
        {!operationsWarming && operationsError && (
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
        {!operationsWarming && !operationsError && operations?.jobs.map((job) => {
          const run = job.latest_run;
          const tone = operationStatusTone(job);
          const busy = operationRunningKey === job.key;
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
                    disabled={!job.configured || Boolean(operationRunningKey) || Boolean(run?.active)}
                    title={job.configured ? `Start ${job.label}` : 'Job binding unavailable'}
                  >
                    <Icon name="play" size={12} />
                    {busy ? 'Starting' : 'Run'}
                  </button>
                </div>
                <span className="muted fs-11">
                  {formatOperationRun(run)}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
