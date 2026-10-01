import { useMemo } from 'react';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { usePresenterMode } from '../../lib/presenterMode';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import type {
  ActivationDestination,
  ActivationSummary,
  ActivationOutboxItem,
} from '../../types';
import { Chip, SurfaceTitle } from '../Primitives';
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
      detail: 'Destination registry unavailable; CRM/Salesforce delivery is unverified until it responds.',
    };
  }
  if (loading) {
    return {
      label: 'CRM / Salesforce handoff',
      value: 'Checking',
      status: 'probing registry',
      tone: 'neutral',
      detail: 'Reading destinations; delivery is unverified until they load.',
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
      detail: 'Salesforce destination connected; delivery is confirmed only where Activation / outreach shows delivered rows.',
    };
  }
  if (dryRun.length > 0 || connected.length > 0) {
    return {
      label: 'CRM / Salesforce handoff',
      value: connected.length > 0 ? `${connected.length} connected destination${connected.length === 1 ? '' : 's'}` : 'Dry run only',
      status: connected.length > 0 ? 'partially connected' : 'dry run',
      tone: connected.length > 0 ? 'success' : 'warning',
      detail: 'Delivery is confirmed only for connected destinations with delivered rows.',
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
      detail: 'Outbox unavailable; delivery and staging status is unverified until it recovers.',
    };
  }
  if (loading) {
    return {
      label: 'Activation / outreach',
      value: 'Checking',
      status: 'probing outbox',
      tone: 'neutral',
      detail: 'Reading activation rows.',
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
      detail: 'Source readiness unavailable; coverage is unverified until it recovers.',
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
    detail: 'Live, synthetic, and pending feeds stay separated.',
  };
}

/**
 * Presenter mode (D-shell-deviations-e1): whether this deployment shows the
 * demo-only affordances. The OFF detail names only the PROTOTYPE borrower
 * view until the roadmap rail slots are gated too (D-shell-deviations-e2).
 */
function presenterModeSummary(presenterMode: boolean): ReadinessItem {
  return presenterMode
    ? {
        label: 'Presenter mode',
        value: 'On',
        status: 'demo affordances visible',
        tone: 'warning',
        detail: 'Roadmap rail slots and the PROTOTYPE borrower view are visible to every user. Demo workspaces only.',
      }
    : {
        label: 'Presenter mode',
        value: 'Off',
        status: 'customer mode',
        tone: 'success',
        detail: 'The PROTOTYPE borrower view is hidden.',
      };
}

export function buyerReadinessItems(
  activation: ActivationSummary | null | undefined,
  sources: SourceSummary[] | undefined,
  sourcesLoading = false,
  sourcesError = false,
  activationError = false,
  activationLoading = false,
  presenterMode = false,
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
      detail: 'Governed SQL and Python rules plus Cotality propensity; not a trained machine-learning model.',
    },
    {
      label: 'Custom segments',
      value: 'Governed cohorts',
      status: 'configured only',
      tone: 'neutral',
      detail: 'Named segments come from governed cohorts; arbitrary segment authoring is available only when a customer segment is configured.',
    },
    {
      label: 'Compliance posture',
      value: 'Governed controls',
      status: 'no certification',
      tone: 'neutral',
      detail: 'Controls: Unity Catalog governance, Lakebase audit, redaction, disclosures and approvals. No third-party certification such as HITRUST is claimed.',
    },
    {
      label: 'Audit coverage',
      value: 'Decision ledger',
      status: 'decisions and borrower reads audited',
      tone: 'success',
      detail: 'Audited: approve, hold and reject decisions; dispositions, assignments and outcomes; staging and exports; Genie questions and governed Genie actions; property lookups; and borrower-level reads (ranked lead lists with their filters, Borrower 360 dossiers and proof, offer recommendations, outreach drafts). Not audited: navigation and aggregate dashboards.',
    },
    presenterModeSummary(presenterMode),
  ];
}

export function BuyerReadinessPanel({ sources, sourcesLoading = false, sourcesError = false }: BuyerReadinessPanelProps) {
  const presenterMode = usePresenterMode();
  const {
    data,
    warmingUp,
    error,
  } = useWarmingUpRetry<ActivationSummary>(
    (signal) => api.activationSummary(signal),
    { queryKey: queryKeys.activationSummary() },
  );
  const items = useMemo(
    () => buyerReadinessItems(
      data,
      sources,
      sourcesLoading,
      sourcesError,
      Boolean(error),
      data === null && !error,
      presenterMode,
    ),
    [data, error, presenterMode, sources, sourcesError, sourcesLoading],
  );
  const attention = items.filter((item) => item.tone === 'warning' || item.tone === 'danger').length;

  return (
    <div className="surface mt-grid" id="buyer-readiness" tabIndex={-1}>
      <div className="surface__hdr surface__hdr--split">
        <div>
          <SurfaceTitle>Deployment readiness</SurfaceTitle>
          <div className="muted fs-12">
            Live, staged, or customer-configured.
          </div>
        </div>
        <Chip variant={error ? 'warning' : attention > 0 ? 'warning' : 'success'}>
          {error ? 'activation unknown' : attention > 0 ? `${attention} caveat${attention === 1 ? '' : 's'}` : 'ready'}
        </Chip>
      </div>
      <div className="surface__body surface__body--stack-sm">
        {warmingUp && <WarmingUpBlock state={warmingUp} title="Deployment readiness loading" compact />}
        {error && !warmingUp && (
          <div className="muted body fs-12">
            Activation status unavailable; connector delivery is unverified.
          </div>
        )}
        <div className="admin-rollups" role="region" aria-label="Deployment readiness boundaries">
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
          Governed today: staging, deterministic scoring, human approval, source readiness and audited decisions.
        </div>
      </div>
    </div>
  );
}
