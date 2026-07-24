import { Link, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { PageShell } from '../components/layout/PageShell';
import { Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { Skeleton } from '../components/ui/Skeleton';
import { api, ApiError } from '../lib/api';
import { assetHrefForSource } from '../lib/drawerSources';
import { queryKeys } from '../lib/queryKeys';
import { formatTimestamp } from '../lib/time';
import type { AssetFreshness, AssetMetadataResponse } from '../types';

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'Unavailable';
  return value.toLocaleString();
}

function freshnessCopy(freshness: AssetFreshness): string {
  if (freshness === 'fresh') return 'Fresh: updated within 7 days';
  if (freshness === 'aging') return 'Aging: updated 7-30 days ago';
  if (freshness === 'stale') return 'Stale: older than 30 days';
  return 'Freshness unavailable';
}

export function formatDateTimeShort(value?: string | null): string {
  if (!value) return 'Unavailable';
  // lib/time parses naive-UTC wire strings correctly and attaches an
  // explicit timezone name (2026-06-11 audit fix).
  return formatTimestamp(value);
}

function statusVariant(status: AssetMetadataResponse['status']): 'success' | 'warning' | 'neutral' {
  if (status === 'live') return 'success';
  if (status === 'error' || status === 'permission_denied') return 'warning';
  return 'neutral';
}

function assetKeyFromPath(path: string | undefined): string {
  return decodeURIComponent(path ?? '').trim();
}

export default function AssetRoute() {
  const params = useParams();
  const assetKey = assetKeyFromPath(params.assetKey);
  const query = useQuery({
    queryKey: queryKeys.assetMetadata(assetKey),
    queryFn: ({ signal }) => api.assetMetadata(assetKey, signal),
    enabled: assetKey.length > 0,
    retry: false,
  });
  const error = query.error instanceof ApiError ? query.error : null;
  const asset = query.data;

  return (
    <PageShell
      eyebrow="Governed asset"
      title={asset?.title ?? 'Asset detail'}
      lede={asset?.description ?? 'Sanitized Unity Catalog metadata for trusted Module 0 proof assets.'}
      heroRight={
        <Link className="btn btn--ghost" to="/">
          <Icon name="home" size={14} />
          Back to app
        </Link>
      }
    >
      {query.isPending && (
        <div className="asset-layout">
          <section className="surface">
            <div className="surface__hdr"><Skeleton width={220} height={18} rounded="sm" /></div>
            <div className="surface__body asset-card-grid">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} width="100%" height={72} rounded="md" />
              ))}
            </div>
          </section>
        </div>
      )}

      {query.isError && (
        <section className="surface">
          <div className="surface__hdr">
            <Icon name="shield" size={14} className="icon-accent" />
            <div className="h-4">{error?.status === 403 ? 'Admin access required' : 'Asset unavailable'}</div>
          </div>
          <div className="surface__body">
            <p className="body flush">
              {error?.status === 403
                ? 'Detailed UC metadata includes schema, tags, DDL contract, and observed lineage, so it is available only to governed administrators.'
                : 'This asset is not in the Module 0 trusted registry, or the warehouse metadata endpoint is unavailable.'}
            </p>
            <div className="chip-row mt-3">
              <Link className="btn btn--primary btn--sm" to="/">
                Return home
              </Link>
            </div>
          </div>
        </section>
      )}

      {asset && (
        <div className="asset-layout">
          <section className="surface asset-hero">
            <div className="surface__hdr surface__hdr--split">
              <div className="surface__hdr-main">
                <div className="surface__icon"><Icon name="db" size={14} /></div>
                <div>
                  <div className="h-4">{asset.title}</div>
                  <div className="muted fs-12 mono">{asset.uc_object}</div>
                </div>
              </div>
              <div className="chip-row">
                <Chip variant={statusVariant(asset.status)}>{asset.status}</Chip>
                <span className={`source-freshness source-freshness--${asset.freshness}`}>
                  {asset.freshness}
                </span>
              </div>
            </div>
            <div className="surface__body">
              <div className="asset-card-grid">
                <MetricCard label="Rows" value={formatNumber(asset.row_count)} detail={asset.row_count_source} />
                <MetricCard label="Files" value={formatNumber(asset.num_files)} detail="Delta metadata" />
                <MetricCard label="Size" value={asset.size_label ?? 'Unavailable'} detail="storage bytes" />
                <MetricCard label="Freshness" value={freshnessCopy(asset.freshness)} detail={formatDateTimeShort(asset.last_updated ?? asset.delta_last_modified)} />
                <MetricCard label="Catalog" value={asset.catalog} detail={`${asset.schema_name}.${asset.object_name}`} />
                <MetricCard label="Generated" value={formatDateTimeShort(asset.generated_at)} detail="metadata read time" />
              </div>
              <div className="asset-actions">
                {asset.catalog_explorer_url && (
                  <a className="btn btn--primary btn--sm" href={asset.catalog_explorer_url} target="_blank" rel="noreferrer">
                    <Icon name="export" size={12} />
                    Open in Catalog Explorer
                  </a>
                )}
                <span className="muted fs-12">
                  Catalog Explorer opens only for users with workspace and UC grants.
                </span>
              </div>
            </div>
          </section>

          <section className="surface">
            <div className="surface__hdr">
              <Icon name="info" size={14} className="icon-accent" />
              <div>
                <div className="h-4">What this proves</div>
                <div className="muted fs-12">Plain-language source contract</div>
              </div>
            </div>
            <div className="surface__body">
              <p className="body flush">{asset.source_note ?? asset.description}</p>
              {asset.known_data_gaps.length > 0 && (
                <div className="asset-callout">
                  {asset.known_data_gaps.map((gap) => (
                    <span key={gap}>{gap}</span>
                  ))}
                </div>
              )}
            </div>
          </section>

          <section className="surface">
            <div className="surface__hdr">
              <Icon name="audit" size={14} className="icon-accent" />
              <div>
                <div className="h-4">Columns</div>
                <div className="muted fs-12">Sensitive identifiers are omitted from this view.</div>
              </div>
            </div>
            <div className="surface__body asset-table-wrap">
              <table className="tbl asset-table">
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Type</th>
                    <th>Comment</th>
                  </tr>
                </thead>
                <tbody>
                  {asset.columns.map((column) => (
                    <tr key={column.name}>
                      <td className="mono">{column.name}</td>
                      <td className="mono">{column.data_type ?? 'unknown'}</td>
                      <td>{column.comment ?? '—'}</td>
                    </tr>
                  ))}
                  {asset.columns.length === 0 && (
                    <tr><td colSpan={3}>No safe column metadata returned.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="surface">
            <div className="surface__hdr">
              <Icon name="doc" size={14} className="icon-accent" />
              <div>
                <div className="h-4">Sanitized DDL contract</div>
                <div className="muted fs-12">Storage locations, owners, grants, properties, and sensitive columns are omitted.</div>
              </div>
            </div>
            <div className="surface__body">
              {asset.ddl ? (
                <pre className="asset-ddl"><code>{asset.ddl}</code></pre>
              ) : (
                <p className="muted flush">No safe DDL contract returned.</p>
              )}
              {asset.ddl_redacted_lines > 0 && (
                <p className="muted fs-12">{asset.ddl_redacted_lines} sensitive DDL line(s) hidden.</p>
              )}
            </div>
          </section>

          <section className="surface">
            <div className="surface__hdr">
              <Icon name="tag" size={14} className="icon-accent" />
              <div>
                <div className="h-4">Tags and safe properties</div>
                <div className="muted fs-12">Only non-sensitive tag/property keys are shown.</div>
              </div>
            </div>
            <div className="surface__body">
              <TagList tags={asset.tags} properties={asset.properties} />
            </div>
          </section>

          <section className="surface">
            <div className="surface__hdr">
              <Icon name="flow" size={14} className="icon-accent" />
              <div>
                <div className="h-4">Observed lineage</div>
                <div className="muted fs-12">System-table lineage observed in the last 90 days; absence is not proof of no lineage.</div>
              </div>
            </div>
            <div className="surface__body asset-lineage">
              {asset.lineage.map((node) => {
                const href = assetHrefForSource(node.asset_path);
                if (!href) {
                  return (
                    <div key={`${node.direction}-${node.asset_path}`} className="lineage-node">
                      <div className="lineage-node__label">{node.direction}</div>
                      <div className="lineage-node__name">{node.label}</div>
                      {node.event_time && <div className="lineage-node__meta">{node.event_time}</div>}
                    </div>
                  );
                }
                return (
                  <Link key={`${node.direction}-${node.asset_path}`} className="lineage-node lineage-node--link" to={href}>
                    <div className="lineage-node__label">{node.direction}</div>
                    <div className="lineage-node__name">{node.label}</div>
                    {node.event_time && <div className="lineage-node__meta">{node.event_time}</div>}
                  </Link>
                );
              })}
              {asset.lineage.length === 0 && (
                <p className="muted flush">No allowlisted observed lineage returned for this asset.</p>
              )}
            </div>
          </section>
        </div>
      )}
    </PageShell>
  );
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="asset-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <em>{detail}</em>
    </div>
  );
}

function TagList({
  tags,
  properties,
}: {
  tags: AssetMetadataResponse['tags'];
  properties: AssetMetadataResponse['properties'];
}) {
  if (tags.length === 0 && properties.length === 0) {
    return <p className="muted flush">No safe tags or properties returned.</p>;
  }
  return (
    <div className="asset-tag-grid">
      {tags.map((tag) => (
        <div key={`tag-${tag.name}`} className="lineage-node">
          <div className="lineage-node__label">Tag</div>
          <div className="lineage-node__name">{tag.name}</div>
          {tag.value && <div className="lineage-node__meta">{tag.value}</div>}
        </div>
      ))}
      {properties.map((property) => (
        <div key={`property-${property.name}`} className="lineage-node">
          <div className="lineage-node__label">Property</div>
          <div className="lineage-node__name">{property.name}</div>
          {property.value && <div className="lineage-node__meta">{property.value}</div>}
        </div>
      ))}
    </div>
  );
}
