import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { DataEstateAsset, DataEstateLane, DataEstateResponse, DataEstateStatus } from '../../types';
import { Chip } from '../Primitives';
import { Icon } from '../Icon';
import { useApp } from '../AppContext';
import { assetHrefForSource, descriptorFor, DRAWER_SOURCES } from '../../lib/drawerSources';
import { formatTimestamp } from '../../lib/time';
import { Skeleton } from '../ui/Skeleton';

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

function assetDomId(laneId: string, assetName: string, index: number): string {
  const slug = `${laneId}-${assetName}`
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return `data-estate-asset-${index}-${slug || 'asset'}`;
}

export function DataEstatePanel({ estate }: { estate: DataEstateResponse }) {
  const { setDrawer } = useApp();
  const [expandedAssetKey, setExpandedAssetKey] = useState<string | null>(null);

  return (
    <div className="surface data-estate">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="layers" size={14} />
          </div>
          <div>
            <div className="h-4">Data estate under the hood</div>
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
            <DataEstateLaneCard
              key={lane.id}
              lane={lane}
              expandedAssetKey={expandedAssetKey}
              proofAssets={estate.proof_assets}
              setExpandedAssetKey={setExpandedAssetKey}
              setDrawer={setDrawer}
            />
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

interface DataEstateLaneCardProps {
  lane: DataEstateLane;
  expandedAssetKey: string | null;
  proofAssets: string[];
  setExpandedAssetKey: (assetKey: string | null) => void;
  setDrawer: ReturnType<typeof useApp>['setDrawer'];
}

function DataEstateLaneCard({
  lane,
  expandedAssetKey,
  proofAssets,
  setExpandedAssetKey,
  setDrawer,
}: DataEstateLaneCardProps) {
  return (
    <section className="data-estate__lane" aria-label={lane.title}>
      <div className="data-estate__lane-hdr">
        <div className="data-estate__lane-title-wrap">
          <span>
            <span className="data-estate__lane-title">{lane.title}</span>
            <span className="data-estate__lane-copy">{lane.description}</span>
          </span>
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
        {lane.assets.map((asset, index) => {
          const assetKey = `${lane.id}-${asset.name}`;
          const detailId = assetDomId(lane.id, asset.name, index);
          const assetExpanded = expandedAssetKey === assetKey;
          return (
            <div key={assetKey} className={`data-estate__asset-wrap ${assetExpanded ? 'is-expanded' : ''}`}>
              <button
                type="button"
                className="data-estate__asset"
                onClick={() => {
                  setExpandedAssetKey(assetExpanded ? null : assetKey);
                }}
                aria-expanded={assetExpanded}
                aria-controls={detailId}
                title={`Show contract details for ${asset.label}`}
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
                  <Icon name={assetExpanded ? 'up' : 'down'} size={12} />
                </div>
              </button>
              {asset.catalog_explorer_url && (
                <a
                  className="data-estate__asset-catalog"
                  href={asset.catalog_explorer_url}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={`Open ${asset.label} in Catalog Explorer`}
                >
                  <Icon name="export" size={12} />
                  Catalog Explorer
                </a>
              )}
              {assetExpanded && (
                <AssetDetail
                  asset={asset}
                  detailId={detailId}
                  proofAssets={proofAssets}
                  setDrawer={setDrawer}
                />
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function AssetDetail({
  asset,
  detailId,
  proofAssets,
  setDrawer,
}: {
  asset: DataEstateAsset;
  detailId: string;
  proofAssets: string[];
  setDrawer: ReturnType<typeof useApp>['setDrawer'];
}) {
  const proofed = asset.uc_object ? proofAssets.includes(asset.uc_object) : false;
  const assetHref = assetHrefForSource(asset.uc_object);

  return (
    <div className="data-estate__asset-detail" id={detailId}>
      <div className="data-estate__detail-grid">
        <div className="data-estate__detail-item">
          <span className="data-estate__detail-label">Status</span>
          <span>{statusLabel(asset.status)}</span>
        </div>
        <div className="data-estate__detail-item">
          <span className="data-estate__detail-label">Rows</span>
          <span>{formatRows(asset.row_count) ?? 'Not published'}</span>
        </div>
        <div className="data-estate__detail-item">
          <span className="data-estate__detail-label">Freshness</span>
          <span>{asset.last_updated ? formatTimestamp(asset.last_updated) : 'Freshness unavailable'}</span>
        </div>
        <div className="data-estate__detail-item">
          <span className="data-estate__detail-label">Governed object</span>
          <span className="mono">{asset.uc_object ?? 'Object not exposed'}</span>
        </div>
      </div>
      <p className="body muted flush">{asset.note}</p>
      <div className="chip-row">
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => setDrawer(descriptorFor(asset.uc_object ?? asset.name))}
        >
          <Icon name="link" size={12} />
          Open lineage
        </button>
        {assetHref && (
          <Link className="btn btn--ghost btn--sm" to={assetHref}>
            <Icon name="db" size={12} />
            Asset details
          </Link>
        )}
        {asset.catalog_explorer_url && (
          <a
            className="btn btn--ghost btn--sm"
            href={asset.catalog_explorer_url}
            target="_blank"
            rel="noreferrer"
          >
            <Icon name="export" size={12} />
            Catalog Explorer
          </a>
        )}
        <span className={`chip chip--${proofed ? 'success' : 'neutral'}`}>
          {proofed ? 'Proof asset' : 'No proof asset'}
        </span>
      </div>
    </div>
  );
}

export function DataEstatePanelSkeleton() {
  return (
    <div className="surface data-estate" aria-busy="true" role="status">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="layers" size={14} />
          </div>
          <div>
            <div className="h-4">Data estate under the hood</div>
            <div className="muted fs-12">
              Loading source readiness, freshness, and governed lineage proof…
            </div>
          </div>
        </div>
        <Skeleton width={128} height={24} rounded="pill" />
      </div>
      <div className="surface__body">
        <div className="data-estate__grid">
          {Array.from({ length: 4 }).map((_, lane) => (
            <section key={lane} className="data-estate__lane" aria-label="Data estate lane loading">
              <div className="data-estate__lane-hdr">
                <div className="data-estate__lane-skeleton-main">
                  <Skeleton width={160} height={13} rounded="sm" />
                  <Skeleton width="92%" height={11} rounded="sm" />
                </div>
                <Skeleton width={72} height={22} rounded="pill" />
              </div>
              <div className="data-estate__assets">
                {Array.from({ length: 5 }).map((__, asset) => (
                  <div key={asset} className="data-estate__asset data-estate__asset--skeleton">
                    <div className="data-estate__asset-main">
                      <Skeleton width={8} height={8} rounded="pill" />
                      <Skeleton width="78%" height={11} rounded="sm" />
                    </div>
                    <Skeleton width={54} height={11} rounded="sm" />
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
