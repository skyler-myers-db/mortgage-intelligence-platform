import { Fragment, lazy, Suspense, useContext } from 'react';
import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { Icon } from '../Icon';
import { api } from '../../lib/api';
import { kpiProofApi } from '../../lib/apiClients/kpiProof';
import { assetKeyForSource } from '../../lib/drawerSources';
import { resolveDrawerProse } from '../../lib/drawerSourceRegistry.prose';
import { queryKeys } from '../../lib/queryKeys';
import { formatTimestamp } from '../../lib/time';
import { formatCount } from '../../lib/formatters';
import type {
  AssetLineageNode,
  AssetMetadataResponse,
  LineageLayer,
  LineageManifestResponse,
  LineageManifestNode,
} from '../../types';
import { EvidenceDrawerBodyContext, type EvidenceDrawerBodyProps } from './evidenceDrawerBodyLoader';
import { deltaExplainerOf } from '../../lib/deltaExplainerSource';
import { EvidenceFreshness } from './EvidenceFreshness';
import { EvidenceHowWeGot } from './EvidenceHowWeGot';
import { EvidenceKpiProof } from './EvidenceKpiProof';

/**
 * The Delta Explainer (audit wow-ai-3, deviation:delta-explainer): its own
 * chunk, mounted only while the drawer is open on Overview for a source that
 * carries one (after How we got), so its audit-free read never runs on hover
 * or for any other source. The evidence hover card never renders it.
 */
const DeltaExplainer = lazy(() => import('./DeltaExplainer'));

/**
 * Data source / evidence drawer — fast context for a source chip.
 * The drawer starts with human explanation and, when the source maps to a
 * trusted Module 0 asset, enriches itself with governed UC metadata.
 * The Lineage tab renders the governed lineage manifest
 * (backend/resources/lineage_manifest.json via /api/lineage/manifest):
 * raw Cotality share → silver → UC function → gold → metric view, each
 * node a chip deep-linking to Catalog Explorer.
 *
 * This module is the drawer's lazy BODY (audit 2026-09-21 `bundle-04`):
 * EvidenceDrawer.tsx is the shell frame around it. It resolves the source's
 * registry prose (lib/drawerSourceRegistry.prose, which ships with this
 * chunk) before rendering. Overview leads with "How we got {value}" (audit
 * flow-06); Under the hood holds the KPI reproduce SQL, the sanitized
 * signals and the administrator's catalog detail.
 */

const LINEAGE_LAYER_LABELS: Record<LineageLayer, string> = {
  raw_share: 'Raw Cotality share',
  silver: 'Silver',
  gold: 'Gold',
  uc_function: 'UC function',
  metric_view: 'Metric view',
  reference: 'Reference',
};

/**
 * A governed asset name carries the prototype's `.lineage-node__name`
 * (design_files/index.html:698) wherever it renders — chip row or chain node.
 * That class is the evidence contract: "this KPI traces to this Unity Catalog
 * object" is asserted against `.lineage-node__name`.
 *
 * `display` overrides the visible text without changing where the chip links.
 * Signal rows use it to keep their column-level pointer
 * (`portfolio_headline_metric_view.in_the_money`) while still deep-linking to
 * the table; the governed-asset row and the lineage chain show the manifest's
 * fully-qualified `catalog.schema.object`. Keeping the table FQN to a single
 * slot per drawer is also what makes it addressable by an exact locator.
 */
function LineageManifestChip({
  node,
  display,
}: {
  node: LineageManifestNode;
  display?: string;
}) {
  const text = display ?? node.fqn;
  if (node.catalog_explorer_url) {
    return (
      <a
        className="chip chip--neutral lineage-node__chip"
        href={node.catalog_explorer_url}
        target="_blank"
        rel="noreferrer"
        aria-label={`${node.label} — open ${node.fqn} in Catalog Explorer`}
      >
        <span className="lineage-node__name">{text}</span>
        <Icon name="export" size={10} />
      </a>
    );
  }
  return (
    <span
      className="chip chip--neutral lineage-node__chip"
      title="Catalog Explorer link unavailable — no workspace host configured"
    >
      <span className="lineage-node__name">{text}</span>
    </span>
  );
}

function ObservedLineageAsset({ node }: { node: AssetLineageNode }) {
  const content = (
    <>
      <div className="lineage-node__label">Observed {node.direction}</div>
      <div className="lineage-node__name">{node.asset_path}</div>
      <div className="lineage-node__meta">{node.label}</div>
      {node.event_time && (
        <div className="lineage-node__meta">
          {formatTimestamp(node.event_time, { withYear: false })}
        </div>
      )}
      {node.event_count !== null && node.event_count !== undefined && (
        <div className="lineage-node__meta">
          {formatCount(node.event_count)} observed event(s)
        </div>
      )}
    </>
  );
  if (node.catalog_explorer_url) {
    return (
      <a
        className="lineage-node lineage-node--link observed-lineage__asset"
        href={node.catalog_explorer_url}
        target="_blank"
        rel="noreferrer"
        aria-label={`Observed ${node.direction} asset ${node.asset_path} - open in Catalog Explorer`}
      >
        {content}
      </a>
    );
  }
  return (
    <div
      className="lineage-node observed-lineage__asset"
      title="Catalog Explorer link unavailable - no workspace host configured"
    >
      {content}
    </div>
  );
}

function catalogNodeForSource(
  rawSource: string,
  manifest?: LineageManifestResponse,
): LineageManifestNode | null {
  const directKey = assetKeyForSource(rawSource);
  const parts = rawSource.trim().replace(/`/g, '').split('.');
  const assetKey =
    directKey ??
    (parts.length >= 4
      ? assetKeyForSource(parts.slice(0, 3).join('.'))
      : parts.length === 2
        ? assetKeyForSource(parts[0])
        : null);
  if (!assetKey || !manifest) return null;
  for (const family of manifest.families) {
    const node = family.nodes.find((candidate) => assetKeyForSource(candidate.fqn) === assetKey);
    if (node) return node;
  }
  return null;
}

function compactFamilyNodes(nodes: LineageManifestNode[]): LineageManifestNode[] {
  return [...new Map(nodes.map((node) => [node.fqn.toLowerCase(), node])).values()];
}

/** Row and file counts read exactly, as the asset detail page shows them (lib/formatters). */
function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'Unavailable';
  return formatCount(value);
}

function metadataStatRows(metadata?: AssetMetadataResponse) {
  if (!metadata) return [];
  return [
    ['Rows', formatNumber(metadata.row_count)],
    ['Files', formatNumber(metadata.num_files)],
    ['Size', metadata.size_label ?? 'Unavailable'],
    [
      'Modified',
      metadata.delta_last_modified
        ? formatTimestamp(metadata.delta_last_modified)
        : 'Unavailable',
    ],
  ];
}

/**
 * The panels of the evidence drawer (audit 2026-09-21 `bundle-04`): the
 * governed reads (freshness, the KPI proof, the lineage manifest, admin
 * asset metadata) and the Overview / Lineage / Under the hood panels. Loaded
 * lazily by the EvidenceDrawer frame, which renders it only once a source
 * has been opened and hands it the source and everything derived from it
 * alone (evidenceDrawerBodyLoader). Every read starts only while it is open.
 */
function useEvidenceDrawerBodyProps(): EvidenceDrawerBodyProps {
  const props = useContext(EvidenceDrawerBodyContext);
  if (!props) throw new Error('EvidenceDrawerBody renders only inside the EvidenceDrawer frame');
  return props;
}

export function EvidenceDrawerBody() {
  const { source, open, tab, panelProps, destination, assetDetailsHref, eventDateLabel, canAccessAdmin, onClose } =
    useEvidenceDrawerBodyProps();
  const d = resolveDrawerProse(source);
  // /api/admin/assets/:key/metadata is AdminDep-gated. A loan officer opening
  // an evidence drawer used to fire it and eat a 403 on every open — invisible
  // in the UI, loud in the browser console (2026-08-07 audit H4). Ask only
  // when the server-authoritative session says this actor may. It drives only
  // the administrator's stat rows, workspace verification and observed
  // lineage; the freshness chip reads GET /assets/{key}/freshness for every
  // role (EvidenceFreshness, critic-03).
  const metadataQuery = useQuery({
    queryKey: queryKeys.assetMetadata(d.assetKey),
    queryFn: ({ signal }) => api.assetMetadata(d.assetKey ?? '', signal),
    enabled: open && !!d.assetKey && canAccessAdmin,
    retry: false,
  });
  // Both tabs use the same governed manifest. Overview presents its compact
  // asset list; Lineage presents the full annotated chain.
  const lineageQuery = useQuery({
    queryKey: queryKeys.lineageManifest(),
    queryFn: ({ signal }) => api.lineageManifest(signal),
    enabled: open && !!d.lineageFamily,
    staleTime: Infinity,
    retry: false,
  });
  // One KPI's server-emitted reproduce SQL (flow-06 phase 2): one GET per KPI
  // per document, shared by How we got's filter chips and Under the hood.
  const proofKey = d.proofKey;
  const proofQuery = useQuery({
    queryKey: queryKeys.kpiProof(proofKey),
    queryFn: ({ signal }) =>
      proofKey ? kpiProofApi.kpiProof(proofKey, signal) : Promise.reject(new Error('no KPI proof key')),
    enabled: open && !!proofKey,
    staleTime: Infinity,
    retry: false,
  });
  const lineageFamily = d.lineageFamily
    ? lineageQuery.data?.families.find((family) => family.id === d.lineageFamily) ?? null
    : null;
  const metadata = metadataQuery.data;
  const compactNodes = lineageFamily ? compactFamilyNodes(lineageFamily.nodes) : [];
  const catalogLinksUnavailable = Boolean(
    lineageFamily?.nodes.some((node) => !node.catalog_explorer_url),
  );
  // Lakebase destinations are non-admin app surfaces; the readiness ledger is
  // an asset-detail page, so it is an admin action too.
  const destinationAction =
    destination.kind === 'lakebase' || (destination.kind === 'readiness' && canAccessAdmin)
      ? destination
      : null;
  const catalogExplorerUrl =
    destination.kind !== 'lakebase' ? metadata?.catalog_explorer_url ?? null : null;
  const explainer = open ? deltaExplainerOf(d) : null;

  return (
    <div className="drawer__body">
          {tab === 'lineage' && catalogLinksUnavailable && (
            <div
              className="source-card source-card--warning"
              role="status"
              aria-live="polite"
            >
              One or more Catalog Explorer links are unavailable because the
              Databricks workspace host or asset mapping is not configured. The
              governed asset names remain visible, but click-through lineage is not
              fully ready in this deployment.
            </div>
          )}
          {tab === 'lineage' && (
            <div {...panelProps('lineage')}>
              {!d.lineageFamily && destination.kind === 'lakebase' ? (
                <div className="source-card" role="status">
                  <div className="eyebrow mb-2">{destination.label}</div>
                  <p className="body flush">{destination.description}</p>
                  <div className="chip-row mt-3" aria-label="Lakebase operational records">
                    {destination.objectPaths.map((path) => (
                      <span key={path} className="chip chip--neutral">{path}</span>
                    ))}
                  </div>
                  <div className="drawer__actions">
                    <Link className="btn btn--primary btn--sm" to={destination.href} onClick={onClose}>
                      <Icon name="search" size={12} />
                      {destination.actionLabel}
                    </Link>
                  </div>
                </div>
              ) : !d.lineageFamily ? (
                <div className="source-card" role="status">
                  Lineage not mapped - this source has no governed manifest entry.
                  No chain is substituted.
                </div>
              ) : lineageQuery.isPending ? (
                <div className="source-card" role="status" aria-live="polite">
                  Loading governed lineage manifest…
                </div>
              ) : lineageQuery.isError ? (
                <div className="source-card source-card--warning">
                  The governed lineage manifest could not be loaded. Retry when the
                  backend is reachable.
                </div>
              ) : !lineageFamily ? (
                <div className="source-card source-card--warning">
                  Lineage not mapped - family &lsquo;{d.lineageFamily}&rsquo; is absent
                  from the governed manifest. No chain is substituted.
                </div>
              ) : (
                <>
                  <div className="source-summary">
                    <p className="body flush">{lineageFamily.description}</p>
                    <p className="muted fs-12">
                      Ordered semantics: arrows follow the family&apos;s documented
                      derivation narrative. Adjacent nodes may be parallel co-inputs,
                      not a claim that each object directly writes the next.
                    </p>
                  </div>
                  <div className="eyebrow mt-4 mb-2">{lineageFamily.title} — governed chain</div>
                  {lineageFamily.nodes.map((node, i) => (
                    <Fragment key={node.id}>
                      <div className="lineage-node">
                        <div className="lineage-node__label">
                          {LINEAGE_LAYER_LABELS[node.layer]}
                        </div>
                        <LineageManifestChip node={node} />
                        {node.note && <div className="lineage-node__meta">{node.note}</div>}
                      </div>
                      {i < lineageFamily.nodes.length - 1 && <div className="lineage-arrow" aria-hidden="true">↓</div>}
                    </Fragment>
                  ))}
                  {lineageQuery.data && (
                    <div className="drawer__updated">
                      Source of truth: {lineageQuery.data.manifest_path} (schema v
                      {lineageQuery.data.schema_version}). Live relationships observed
                      during the last 90 days are shown separately below when available.
                    </div>
                  )}
                </>
              )}

              {d.lineageFamily && destination.kind === 'lakebase' && (
                <div className="source-card">
                  <div className="eyebrow mb-2">{destination.label}</div>
                  <p className="body flush">{destination.description}</p>
                  <div className="chip-row mt-3" aria-label="Lakebase operational records">
                    {destination.objectPaths.map((path) => (
                      <span key={path} className="chip chip--neutral">{path}</span>
                    ))}
                  </div>
                  <div className="drawer__actions">
                    <Link className="btn btn--primary btn--sm" to={destination.href} onClick={onClose}>
                      <Icon name="search" size={12} />
                      {destination.actionLabel}
                    </Link>
                  </div>
                </div>
              )}

              {d.assetKey && metadataQuery.isPending && (
                <div className="source-card" role="status" aria-live="polite">
                  Loading observed Unity Catalog relationships…
                </div>
              )}
              {d.assetKey && metadataQuery.isError && (
                <div className="source-card source-card--warning">
                  Observed relationships unavailable. The governed manifest remains primary.
                </div>
              )}
              {metadata && (
                <>
                  <div className="eyebrow mt-5 mb-2">
                    Observed Unity Catalog relationships - supplemental
                  </div>
                  <p className="muted fs-12">
                    Observed in Unity Catalog during the last 90 days. Supplemental to the
                    governed manifest.
                  </p>
                  {metadata.lineage.length > 0 ? (
                    metadata.lineage.map((node) => (
                      <ObservedLineageAsset
                        key={`${node.direction}-${node.asset_path}`}
                        node={node}
                      />
                    ))
                  ) : (
                    <div className="source-card source-card--subtle" role="status">
                      {metadata.known_data_gaps?.some((gap) =>
                        gap.startsWith('Observed lineage unavailable'),
                      )
                        ? 'Observed relationships are unavailable for this asset.'
                        : 'No relationships were observed for this asset in the last 90 days.'}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
          {tab === 'overview' ? (
            <div {...panelProps('overview')}>
              <EvidenceHowWeGot source={d} proof={proofKey ? proofQuery : null} />
              {explainer && (
                <Suspense fallback={<div className="source-card" role="status">Loading the change breakdown…</div>}>
                  <DeltaExplainer explainer={explainer} />
                </Suspense>
              )}
              <EvidenceFreshness
                assetKey={d.assetKey}
                open={open}
                tracked={destination.kind === 'unity_catalog' || destination.kind === 'readiness'}
                destinationLabel={destination.label}
                description={d.description}
              />

              {(destination.kind === 'lakebase' || destination.kind === 'readiness') && (
                <div className="source-card">
                  <div className="eyebrow mb-2">{destination.label}</div>
                  <p className="body flush">{destination.description}</p>
                  <div className="chip-row mt-3" aria-label={`${destination.label} records`}>
                    {destination.objectPaths.map((path) => (
                      <span key={path} className="chip chip--neutral">{path}</span>
                    ))}
                  </div>
                </div>
              )}

              {d.usedIn && d.usedIn.length > 0 && (
                <>
                  <div className="eyebrow mt-4 mb-2">Used in Module 0</div>
                  <div className="chip-row">
                    {d.usedIn.map((use) => (
                      <span key={use} className="chip chip--neutral">{use}</span>
                    ))}
                  </div>
                </>
              )}

              <div className="eyebrow mt-4 mb-2">Governed assets</div>
              {d.lineageFamily && (
                <p className="muted fs-12">
                  The Unity Catalog objects behind this source. The Lineage tab shows how they connect.
                </p>
              )}
              {!d.lineageFamily && destination.kind === 'lakebase' ? (
                <div className="source-card source-card--subtle" role="status">
                  Operational Lakebase records are queried through the linked app surface;
                  they are not Catalog Explorer assets.
                </div>
              ) : !d.lineageFamily ? (
                <div className="source-card source-card--subtle" role="status">
                  Governed assets not mapped. No local lineage is substituted.
                </div>
              ) : lineageQuery.isPending ? (
                <div className="source-card" role="status" aria-live="polite">
                  Loading governed assets…
                </div>
              ) : lineageQuery.isError ? (
                <div className="source-card source-card--warning">
                  Governed assets unavailable; manifest not loaded.
                </div>
              ) : !lineageFamily ? (
                <div className="source-card source-card--warning">
                  Governed assets not mapped - family &lsquo;{d.lineageFamily}&rsquo; is
                  absent from the manifest. No local lineage is substituted.
                </div>
              ) : (
                <div
                  className="chip-row governed-assets__list"
                  role="group"
                  aria-label={`${lineageFamily.title} governed assets`}
                >
                  {compactNodes.map((node) => (
                    <LineageManifestChip key={node.id} node={node} />
                  ))}
                </div>
              )}

              {(assetDetailsHref || destinationAction || catalogExplorerUrl) && (
                <div className="drawer__actions">
                  {assetDetailsHref && (
                    <Link className="btn btn--primary btn--sm" to={assetDetailsHref} onClick={onClose}>
                      <Icon name="db" size={12} />
                      View asset details
                    </Link>
                  )}
                  {destinationAction && (
                    <Link className="btn btn--primary btn--sm" to={destinationAction.href} onClick={onClose}>
                      <Icon name={destinationAction.kind === 'readiness' ? 'db' : 'search'} size={12} />
                      {destinationAction.actionLabel}
                    </Link>
                  )}
                  {catalogExplorerUrl && (
                    <a
                      className="btn btn--ghost btn--sm"
                      href={catalogExplorerUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Icon name="export" size={12} />
                      Catalog Explorer
                    </a>
                  )}
                </div>
              )}

              {eventDateLabel && (
                <div className="drawer__updated">
                  Evidence event date: {eventDateLabel}
                </div>
              )}
            </div>
          ) : null}
          {tab === 'under-the-hood' ? (
            <div {...panelProps('under-the-hood')}>
              {proofKey && <EvidenceKpiProof title={d.title} proof={proofQuery} />}

              <div className="eyebrow mt-4 mb-2">How these assets are listed</div>
              <p className="muted fs-12">
                Each governed object behind this source is listed once on Overview. The Lineage tab shows the same
                objects in the order they are derived, with notes and the last 90 days of observed relationships.
              </p>

              {d.signals && d.signals.length > 0 && (
                <>
                  <div className="eyebrow mt-5 mb-2">Sanitized signals</div>
                  {d.signals.map((s, i) => {
                    const catalogNode = catalogNodeForSource(s.source, lineageQuery.data);
                    return (
                      <div key={`${s.label}-${i}`} className="lineage-node lineage-node--signal">
                        <div>
                          <div className="lineage-node__label">{s.label}</div>
                          {catalogNode ? (
                            <LineageManifestChip node={catalogNode} display={s.source} />
                          ) : (
                            <div className="lineage-node__name">{s.source}</div>
                          )}
                        </div>
                        <div className="mono num lineage-node__value">{s.value}</div>
                      </div>
                    );
                  })}
                </>
              )}

              {metadataQuery.isFetching && (
                <div className="source-card" role="status" aria-live="polite">
                  Loading governed asset metadata…
                </div>
              )}

              {metadataQuery.isError && d.assetKey && (
                <div className="source-card source-card--warning">
                  Governed asset metadata could not be loaded, or the warehouse is warming. The source explanation on
                  Overview remains available.
                </div>
              )}

              {metadata && (
                <>
                  <div className="source-card source-card--subtle" role="status">
                    <div className="eyebrow mb-2">Workspace verification</div>
                    <p className="body flush">
                      {metadata.observed_in_unity_catalog === true
                        ? `Observed as a ${metadata.object_type} in this workspace.`
                        : metadata.observed_in_unity_catalog === false
                          ? 'The declared asset was not found in current Unity Catalog metadata.'
                          : 'Unity Catalog object verification was unavailable for this request.'}
                    </p>
                    {metadata.observation_source && metadata.observation_source !== 'unavailable' && (
                      <p className="muted fs-12 flush">
                        Verified through {metadata.observation_source}.
                      </p>
                    )}
                  </div>
                  <div className="source-stat-grid" role="group" aria-label="Governed asset metadata">
                    {metadataStatRows(metadata).map(([label, value]) => (
                      <div key={label} className="source-stat">
                        <span>{label}</span>
                        <strong>{value}</strong>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          ) : null}
    </div>
  );
}
