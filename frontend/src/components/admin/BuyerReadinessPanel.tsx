import { useMemo } from 'react';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import type {
  ActivationDestination,
  ActivationSummary,
  ActivationOutboxItem,
} from '../../types';
import { Chip } from '../Primitives';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';

interface SourceSummary {
  name: string;
  status: string;
  rows: number | null;
  last_updated: string | null;
  note: string;
}

interface BuyerReadinessPanelProps {
  sources?: SourceSummary[];
  sourcesLoading?: boolean;
  sourcesError?: boolean;
}

type ReadinessTone = 'success' | 'warning' | 'danger' | 'neutral';

interface ReadinessItem {
  label: string;
  value: string;
  status: string;
  tone: ReadinessTone;
  detail: string;
}

function destinationSummary(
  destinations: ActivationDestination[] | undefined,
  error = false,
  loading = false,
): ReadinessItem {
  if (error) {
    return {
      label: 'CRM / Salesforce handoff',
      value: 'Unknown',
      status: 'registry unavailable',
      tone: 'warning',
      detail: 'Registry unavailable. Do not claim live CRM/Salesforce delivery until it reconnects.',
    };
  }
  if (loading) {
    return {
      label: 'CRM / Salesforce handoff',
      value: 'Checking',
      status: 'probing registry',
      tone: 'neutral',
      detail: 'Reading the destination registry. Delivery claims stay unverified until this resolves.',
    };
  }
  const rows = destinations ?? [];
  const connected = rows.filter((row) => row.status === 'connected');
  const dryRun = rows.filter((row) => row.status === 'dry_run');
  const notConfigured = rows.filter((row) => row.status === 'not_configured');
  const salesforce = rows.find((row) => row.destination_type === 'salesforce');
  if (salesforce?.status === 'connected') {
    return {
      label: 'CRM / Salesforce handoff',
      value: 'Connected destination',
      status: 'connected',
      tone: 'success',
      detail: 'Approved work is delivered through configured Salesforce.',
    };
  }
  if (dryRun.length > 0 || connected.length > 0) {
    return {
      label: 'CRM / Salesforce handoff',
      value: connected.length > 0 ? `${connected.length} connected destination${connected.length === 1 ? '' : 's'}` : 'Dry run only',
      status: connected.length > 0 ? 'partially connected' : 'dry run',
      tone: connected.length > 0 ? 'success' : 'warning',
      detail: 'MIP stages approved work. Claim Salesforce delivery only when connected rows show delivered.',
    };
  }
  return {
    label: 'CRM / Salesforce handoff',
    value: notConfigured.length > 0 ? 'Not configured' : 'No destination registry',
    status: 'setup required',
    tone: 'warning',
    detail: 'Governed staging is live; external CRM/CDP/LOS/POS delivery requires a customer-specific connector.',
  };
}

function outboxSummary(
  rows: ActivationOutboxItem[] | undefined,
  error = false,
  loading = false,
): ReadinessItem {
  if (error) {
    return {
      label: 'Activation / outreach',
      value: 'Unknown',
      status: 'unverified',
      tone: 'warning',
      detail: 'Outbox unavailable. Treat delivery and staging claims as unverified.',
    };
  }
  if (loading) {
    return {
      label: 'Activation / outreach',
      value: 'Checking',
      status: 'probing outbox',
      tone: 'neutral',
      detail: 'Reading recent activation rows before claiming delivery or staging status.',
    };
  }
  const outbox = rows ?? [];
  const delivered = outbox.filter((row) => row.status === 'delivered').length;
  const staged = outbox.filter((row) => row.status === 'staged' || row.status === 'dry_run').length;
  return {
    label: 'Activation / outreach',
    value: delivered > 0 ? `${delivered} delivered row${delivered === 1 ? '' : 's'}` : staged > 0 ? `${staged} staged row${staged === 1 ? '' : 's'}` : 'Approval-gated staging',
    status: delivered > 0 ? 'delivery observed' : 'no auto-send',
    tone: delivered > 0 ? 'success' : 'neutral',
    detail: 'MIP drafts, approves, and stages. It does not auto-send email or SMS.',
  };
}

function sourceSummary(sources: SourceSummary[] | undefined, loading = false, error = false): ReadinessItem {
  if (error) {
    return {
      label: 'Data readiness',
      value: 'Unavailable',
      status: 'reconnecting',
      tone: 'warning',
      detail: 'Retry before making source-coverage claims.',
    };
  }
  if (loading || !sources) {
    return {
      label: 'Data readiness',
      value: 'Loading',
      status: 'probing',
      tone: 'neutral',
      detail: 'Checking source-readiness rows.',
    };
  }
  const live = sources.filter((row) => row.status === 'live').length;
  const synthetic = sources.filter((row) => row.status === 'demo_synthetic').length;
  const pending = sources.filter((row) => ['roadmap', 'not_configured', 'configured_empty'].includes(row.status)).length;
  return {
    label: 'Data readiness',
    value: `${live} live · ${synthetic} synthetic · ${pending} pending`,
    status: pending > 0 ? 'partial' : 'ready',
    tone: pending > 0 ? 'warning' : 'success',
    detail: 'Live, synthetic, and pending feeds stay separated; demos cannot imply everything is live.',
  };
}

export function buyerReadinessItems(
  activation: ActivationSummary | null | undefined,
  sources: SourceSummary[] | undefined,
  sourcesLoading = false,
  sourcesError = false,
  activationError = false,
  activationLoading = false,
): ReadinessItem[] {
  return [
    destinationSummary(activation?.destinations, activationError, activationLoading),
    outboxSummary(activation?.recent_outbox, activationError, activationLoading),
    sourceSummary(sources, sourcesLoading, sourcesError),
    {
      label: 'Scoring / recommendations',
      value: 'Deterministic rules',
      status: 'not trained ML',
      tone: 'neutral',
      detail: 'Governed SQL/Python rules plus Cotality propensity. Do not call them a trained MIP ML model.',
    },
    {
      label: 'Compliance posture',
      value: 'Governed controls',
      status: 'no certification claim',
      tone: 'neutral',
      detail: 'UC, Lakebase audit, redaction, disclosures, and approvals are controls. Do not claim HITRUST or certification.',
    },
    {
      label: 'Audit coverage',
      value: 'Decision ledger',
      status: 'key actions audited',
      tone: 'success',
      detail: 'Approvals, rejections, staging, outcomes, and governed Genie actions are audited. Do not claim every click.',
    },
  ];
}

export function BuyerReadinessPanel({ sources, sourcesLoading = false, sourcesError = false }: BuyerReadinessPanelProps) {
  const {
    data,
    warmingUp,
    error,
  } = useWarmingUpRetry<ActivationSummary>(
    (signal) => api.activationSummary(signal),
    [],
    { queryKey: queryKeys.activationSummary() },
  );
  const items = useMemo(
    () => buyerReadinessItems(
      data,
      sources,
      sourcesLoading,
      sourcesError,
      Boolean(error),
      data === undefined && !error,
    ),
    [data, error, sources, sourcesError, sourcesLoading],
  );
  const attention = items.filter((item) => item.tone === 'warning' || item.tone === 'danger').length;

  return (
    <div className="surface mt-grid" id="buyer-readiness">
      <div className="surface__hdr surface__hdr--split">
        <div>
          <div className="h-4">Buyer readiness</div>
          <div className="muted fs-12">
            Live, staged, or customer-configured.
          </div>
        </div>
        <Chip variant={error ? 'warning' : attention > 0 ? 'warning' : 'success'}>
          {error ? 'activation unknown' : attention > 0 ? `${attention} caveat${attention === 1 ? '' : 's'}` : 'ready'}
        </Chip>
      </div>
      <div className="surface__body surface__body--stack-sm">
        {warmingUp && <WarmingUpBlock state={warmingUp} title="Buyer readiness loading" compact />}
        {error && !warmingUp && (
          <div className="muted body fs-12">
            Activation status unavailable; connector claims are unverified.
          </div>
        )}
        <div className="admin-rollups" role="region" aria-label="Buyer readiness claim boundaries">
          <div className="admin-rollups__grid admin-rollups__grid--wide">
            {items.map((item) => (
              <div key={item.label} className="admin-rollup admin-rollup--readiness">
                <div className="split-row">
                  <span className="admin-rollup__label">{item.label}</span>
                  <Chip variant={item.tone}>{item.status}</Chip>
                </div>
                <strong>{item.value}</strong>
                <span className="muted fs-12">{item.detail}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="muted fs-12">
          Safe claims: governed staging, deterministic scoring, human approval, source readiness, and audited decisions.
        </div>
      </div>
    </div>
  );
}
