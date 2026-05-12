import type { DataEstateResponse, DataEstateStatus } from '../../types';
import { Chip } from '../Primitives';
import { Icon } from '../Icon';
import { useApp } from '../AppContext';
import { descriptorFor, DRAWER_SOURCES } from '../../lib/drawerSources';

function statusLabel(status: DataEstateStatus): string {
  if (status === 'demo_synthetic') return 'demo synthetic';
  if (status === 'configured_empty') return 'empty';
  if (status === 'not_configured') return 'not connected';
  if (status === 'permission_denied') return 'grant needed';
  if (status === 'roadmap') return 'pending';
  if (status === 'error') return 'error';
  return 'live';
}

function chipVariant(status: DataEstateStatus): 'success' | 'warning' | 'neutral' {
  if (status === 'live') return 'success';
  if (
    status === 'demo_synthetic' ||
    status === 'configured_empty' ||
    status === 'not_configured' ||
    status === 'roadmap'
  ) return 'warning';
  return 'neutral';
}

function statusDot(status: DataEstateStatus): 'ok' | 'warn' | 'error' {
  if (status === 'live') return 'ok';
  if (status === 'error' || status === 'permission_denied') return 'error';
  return 'warn';
}

function formatRows(rows: number | null | undefined): string | null {
  if (rows === null || rows === undefined) return null;
  return `${rows.toLocaleString()} rows`;
}

function laneStatusSummary(status: DataEstateStatus, assets: { status: DataEstateStatus }[]): string {
  const live = assets.filter((asset) => asset.status === 'live').length;
  const roadmap = assets.filter((asset) => asset.status === 'roadmap').length;
  const synthetic = assets.filter((asset) => asset.status === 'demo_synthetic').length;
  const blocked = assets.filter((asset) =>
    asset.status === 'not_configured' ||
    asset.status === 'permission_denied' ||
    asset.status === 'configured_empty' ||
    asset.status === 'error',
  ).length;
  const parts = [
    live > 0 ? `${live} live` : null,
    synthetic > 0 ? `${synthetic} synthetic` : null,
    roadmap > 0 ? `${roadmap} roadmap` : null,
    blocked > 0 ? `${blocked} blocked` : null,
  ].filter(Boolean);
  return parts.length > 1 ? parts.join(' · ') : statusLabel(status);
}

export function DataEstatePanel({ estate }: { estate: DataEstateResponse }) {
  const { setDrawer } = useApp();

  return (
    <div className="surface data-estate">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="layers" size={14} />
          </div>
          <div>
            <div className="h-4">AI data estate under the hood</div>
            <div className="muted fs-12">
              First-party data, Cotality enrichment, Databricks governance, and Entrada transformations.
            </div>
          </div>
        </div>
        <Chip variant={estate.public_demo_masking ? 'success' : 'warning'} icon="shield">
          {estate.public_demo_masking ? 'Public masking on' : 'Raw-id view'}
        </Chip>
      </div>
      <div className="surface__body">
        <div className="data-estate__grid">
          {estate.lanes.map((lane) => (
            <section key={lane.id} className="data-estate__lane" aria-label={lane.title}>
              <div className="data-estate__lane-hdr">
                <div>
                  <div className="data-estate__lane-title">{lane.title}</div>
                  <div className="data-estate__lane-copy">{lane.description}</div>
                </div>
                <button
                  type="button"
                  className={`chip chip--${chipVariant(lane.status)} data-estate__lane-proof`}
                  onClick={() => setDrawer(DRAWER_SOURCES.sourceReadiness)}
                  title={`Open source-readiness proof for ${lane.title}`}
                >
                  <span className="chip__label">{laneStatusSummary(lane.status, lane.assets)}</span>
                </button>
              </div>
              <div className="data-estate__assets">
                {lane.assets.map((asset) => (
                  <button
                    key={`${lane.id}-${asset.name}`}
                    type="button"
                    className="data-estate__asset"
                    onClick={() => setDrawer(descriptorFor(asset.uc_object ?? asset.name))}
                    title={`Open lineage for ${asset.label}${asset.uc_object ? ` · ${asset.uc_object}` : ''}`}
                  >
                    <div className="data-estate__asset-main">
                      <span className={`status-dot status-dot--${statusDot(asset.status)}`} />
                      <span>{asset.label}</span>
                    </div>
                    <div className="data-estate__asset-meta">
                      {formatRows(asset.row_count) ?? statusLabel(asset.status)}
                      {asset.synthetic_demo && (
                        <span className="chip chip--neutral data-estate__asset-chip">demo synthetic</span>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
        {estate.known_data_gaps.length > 0 && (
          <div className="data-estate__gaps">
            {estate.known_data_gaps.map((gap) => (
              <span key={gap} className="chip chip--warning">
                {gap}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
