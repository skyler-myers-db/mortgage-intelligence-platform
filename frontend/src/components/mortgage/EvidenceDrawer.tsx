import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useApp } from '../AppContext';
import { Icon } from '../Icon';
import { api } from '../../lib/api';
import {
  assetDetailHref,
  assetKeyForSource,
  evidenceDestinationFor,
} from '../../lib/drawerSources';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { queryKeys } from '../../lib/queryKeys';
import { formatTimestamp } from '../../lib/time';
import type {
  AssetFreshness,
  AssetLineageNode,
  AssetMetadataResponse,
  LineageLayer,
  LineageManifestResponse,
  LineageManifestNode,
} from '../../types';

/**
 * Data source / evidence drawer — fast context for a source chip.
 * The drawer starts with human explanation and, when the source maps to a
 * trusted Module 0 asset, enriches itself with governed UC metadata.
 * The Lineage tab renders the governed lineage manifest
 * (backend/resources/lineage_manifest.json via /api/lineage/manifest):
 * raw Cotality share → silver → UC function → gold → metric view, each
 * node a chip deep-linking to Catalog Explorer.
 */

type DrawerTab = 'overview' | 'lineage';

const LINEAGE_LAYER_LABELS: Record<LineageLayer, string> = {
  raw_share: 'Raw Cotality share',
  silver: 'Silver',
  gold: 'Gold',
  uc_function: 'UC function',
  metric_view: 'Metric view',
  reference: 'Reference',
};

function LineageManifestChip({ node }: { node: LineageManifestNode }) {
  if (node.catalog_explorer_url) {
    return (
      <a
        className="chip chip--neutral lineage-node__chip"
        href={node.catalog_explorer_url}
        target="_blank"
        rel="noreferrer"
        aria-label={`${node.label} — open ${node.fqn} in Catalog Explorer`}
      >
        {node.fqn}
        <Icon name="export" size={10} />
      </a>
    );
  }
  return (
    <span
      className="chip chip--neutral lineage-node__chip"
      title="Catalog Explorer link unavailable — no workspace host configured"
    >
      {node.fqn}
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
          {node.event_count.toLocaleString()} observed event(s)
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

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'Unavailable';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return value.toLocaleString();
}

/**
 * Freshness chip state. 'loading'/'error' are VIEW states (governed
 * metadata request in flight / failed), distinct from "the source has no
 * refresh timestamp" — conflating them made a 403 on the metadata read
 * render as "Freshness Unavailable", which reads like a data problem
 * (observed 2026-06-11 during the admin-allowlist incident).
 */
type FreshnessView = AssetFreshness | 'loading' | 'error' | undefined;

function freshnessLabel(view: FreshnessView): string {
  if (view === 'loading') return 'Checking freshness…';
  if (view === 'error') return 'Metadata not loaded';
  if (view === 'fresh') return 'Fresh';
  if (view === 'aging') return 'Aging';
  if (view === 'stale') return 'Stale';
  return 'Freshness unavailable';
}

function freshnessHelp(view: FreshnessView): string {
  if (view === 'loading') return 'Reading governed Unity Catalog metadata.';
  if (view === 'error') {
    return 'Governed freshness could not be read for this view — see the notice below. This does not mean the source is stale.';
  }
  if (view === 'fresh') return 'Updated within 7 days.';
  if (view === 'aging') return 'Updated 7-30 days ago.';
  if (view === 'stale') return 'Updated more than 30 days ago.';
  return 'No backend refresh timestamp is available for this source.';
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

export function EvidenceDrawer() {
  const { drawer, setDrawer } = useApp();
  const open = !!drawer;
  const d = drawer;
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  const drawerRef = useRef<HTMLElement | null>(null);
  const overviewTabRef = useRef<HTMLButtonElement | null>(null);
  const lineageTabRef = useRef<HTMLButtonElement | null>(null);
  const [tab, setTab] = useState<DrawerTab>('overview');
  // Every drawer open starts on Overview — a lineage deep-dive on one
  // source must not leak into the next source's drawer.
  useEffect(() => {
    setTab('overview');
  }, [d]);
  const metadataQuery = useQuery({
    queryKey: queryKeys.assetMetadata(d?.assetKey),
    queryFn: ({ signal }) => api.assetMetadata(d?.assetKey ?? '', signal),
    enabled: open && !!d?.assetKey,
    retry: false,
  });
  // Both tabs use the same governed manifest. Overview presents its compact
  // asset list; Lineage presents the full annotated chain.
  const lineageQuery = useQuery({
    queryKey: queryKeys.lineageManifest(),
    queryFn: ({ signal }) => api.lineageManifest(signal),
    enabled: open && !!d?.lineageFamily,
    staleTime: Infinity,
    retry: false,
  });
  const lineageFamily = d?.lineageFamily
    ? lineageQuery.data?.families.find((family) => family.id === d.lineageFamily) ?? null
    : null;
  const metadata = metadataQuery.data;
  const destination = evidenceDestinationFor(d);
  const compactNodes = lineageFamily ? compactFamilyNodes(lineageFamily.nodes) : [];
  const catalogLinksUnavailable = Boolean(
    lineageFamily?.nodes.some((node) => !node.catalog_explorer_url),
  );
  // View-state for the freshness chip: only mapped assets ever issue the
  // governed metadata read, so loading/error states are scoped to them.
  const freshnessView: FreshnessView = d?.assetKey
    ? metadataQuery.isError
      ? 'error'
      : metadataQuery.isPending
        ? 'loading'
        : metadata?.freshness
    : metadata?.freshness;
  const assetHref = d?.assetKey ? assetDetailHref(d.assetKey) : null;
  const selectTabFromKeyboard = (event: KeyboardEvent<HTMLButtonElement>) => {
    const tabs: DrawerTab[] = ['overview', 'lineage'];
    const currentIndex = tabs.indexOf(tab);
    let next: DrawerTab | null = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      next = tabs[(currentIndex + 1) % tabs.length];
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      next = tabs[(currentIndex - 1 + tabs.length) % tabs.length];
    } else if (event.key === 'Home') {
      next = 'overview';
    } else if (event.key === 'End') {
      next = 'lineage';
    }
    if (!next) return;
    event.preventDefault();
    setTab(next);
    (next === 'overview' ? overviewTabRef : lineageTabRef).current?.focus();
  };
  useFocusTrap({
    open,
    containerRef: drawerRef,
    initialFocusRef: closeBtnRef,
    onClose: () => setDrawer(null),
  });

  return (
    <>
      <div
        className={`drawer-scrim ${open ? 'is-open' : ''}`}
        onClick={() => setDrawer(null)}
        aria-hidden={!open}
      />
      <aside
        ref={drawerRef}
        className={`drawer ${open ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={d ? 'evidence-drawer-title' : undefined}
        aria-label={d ? undefined : 'Data source and lineage'}
        aria-hidden={!open}
      >
        <div className="drawer__hdr">
          <div className="drawer__source-icon">
            <Icon name="db" size={16} />
          </div>
          <div className="drawer__hdr-main">
            <div className="drawer__title" id="evidence-drawer-title">{d?.title ?? 'Data source'}</div>
            <div className="drawer__subtitle">{d?.short ?? d?.assetPath ?? 'Source proof'}</div>
          </div>
          <button ref={closeBtnRef} className="drawer__close" onClick={() => setDrawer(null)} aria-label="Close drawer" type="button">
            <Icon name="close" size={14} />
          </button>
        </div>
        {d && (
          <div
            className="drawer__tabs"
            role="tablist"
            aria-label="Evidence detail views"
            aria-orientation="horizontal"
          >
            <button
              ref={overviewTabRef}
              role="tab"
              id="drawer-tab-overview"
              aria-selected={tab === 'overview'}
              aria-controls="drawer-panel-overview"
              tabIndex={tab === 'overview' ? 0 : -1}
              className={`drawer__tab ${tab === 'overview' ? 'is-active' : ''}`}
              onClick={() => setTab('overview')}
              onKeyDown={selectTabFromKeyboard}
              type="button"
            >
              Overview
            </button>
            <button
              ref={lineageTabRef}
              role="tab"
              id="drawer-tab-lineage"
              aria-selected={tab === 'lineage'}
              aria-controls="drawer-panel-lineage"
              tabIndex={tab === 'lineage' ? 0 : -1}
              className={`drawer__tab ${tab === 'lineage' ? 'is-active' : ''}`}
              onClick={() => setTab('lineage')}
              onKeyDown={selectTabFromKeyboard}
              type="button"
            >
              Lineage
            </button>
          </div>
        )}
        <div className="drawer__body">
          {catalogLinksUnavailable && (
            <div className="source-card source-card--warning" role="alert">
              One or more Catalog Explorer links are unavailable because the
              Databricks workspace host or asset mapping is not configured. The
              governed asset names remain visible, but click-through lineage is not
              fully ready in this deployment.
            </div>
          )}
          {d && tab === 'lineage' && (
            <div role="tabpanel" id="drawer-panel-lineage" aria-labelledby="drawer-tab-lineage">
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
                    <Link className="btn btn--primary btn--sm" to={destination.href} onClick={() => setDrawer(null)}>
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
                      {i < lineageFamily.nodes.length - 1 && <div className="lineage-arrow">↓</div>}
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
                    <Link className="btn btn--primary btn--sm" to={destination.href} onClick={() => setDrawer(null)}>
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
          {d && tab === 'overview' ? (
            <div role="tabpanel" id="drawer-panel-overview" aria-labelledby="drawer-tab-overview">
              <div className="source-summary">
                <div className="source-summary__top">
                  {/* Modifier keys off the VIEW state, not metadata.freshness:
                      'loading' and 'error' previously fell through to the
                      --unavailable style, visually conflating "checking" /
                      "fetch failed" with "no timestamp" (re-audit 2026-06-11
                      cosmetic finding). */}
                  {(destination.kind === 'unity_catalog' || destination.kind === 'readiness') && (
                    <span className={`source-freshness source-freshness--${freshnessView ?? 'unavailable'}`}>
                      {freshnessLabel(freshnessView)}
                    </span>
                  )}
                  <span className="chip chip--neutral">{destination.label}</span>
                  {metadata?.status && <span className="chip chip--neutral">{metadata.status}</span>}
                </div>
                <p className="body flush">{d.description}</p>
                {(destination.kind === 'unity_catalog' || destination.kind === 'readiness') && (
                  <p className="muted fs-12 flush">{freshnessHelp(freshnessView)}</p>
                )}
              </div>

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

              {metadataQuery.isFetching && (
                <div className="source-card" role="status" aria-live="polite">
                  Loading governed asset metadata…
                </div>
              )}

              {metadataQuery.isError && d.assetKey && (
                <div className="source-card source-card--warning">
                  Governed asset metadata requires admin access or the warehouse is warming. The source explanation above remains available.
                </div>
              )}

              {metadata && (
                <div className="source-stat-grid" aria-label="Governed asset metadata">
                  {metadataStatRows(metadata).map(([label, value]) => (
                    <div key={label} className="source-stat">
                      <span>{label}</span>
                      <strong>{value}</strong>
                    </div>
                  ))}
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
              <p className="muted fs-12">
                Compact semantics: Overview de-duplicates this governed family&apos;s Unity Catalog objects. Lineage uses the same family in documented derivation order, with notes and supplemental 90-day observations.
              </p>
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
                  aria-label={`${lineageFamily.title} governed assets`}
                >
                  {compactNodes.map((node) => (
                    <LineageManifestChip key={node.id} node={node} />
                  ))}
                </div>
              )}

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
                            <LineageManifestChip node={catalogNode} />
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

              <div className="drawer__actions">
                {assetHref && destination.kind === 'unity_catalog' && (
                  <Link className="btn btn--primary btn--sm" to={assetHref} onClick={() => setDrawer(null)}>
                    <Icon name="db" size={12} />
                    View asset details
                  </Link>
                )}
                {(destination.kind === 'lakebase' || destination.kind === 'readiness') && (
                  <Link className="btn btn--primary btn--sm" to={destination.href} onClick={() => setDrawer(null)}>
                    <Icon name={destination.kind === 'readiness' ? 'db' : 'search'} size={12} />
                    {destination.actionLabel}
                  </Link>
                )}
                {metadata?.catalog_explorer_url && destination.kind !== 'lakebase' && (
                  <a
                    className="btn btn--ghost btn--sm"
                    href={metadata.catalog_explorer_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <Icon name="export" size={12} />
                    Catalog Explorer
                  </a>
                )}
              </div>

              {d.eventDate && (
                <div className="drawer__updated">
                  Evidence event date: {formatTimestamp(d.eventDate)}
                </div>
              )}
              {metadata?.last_updated && (
                <div className="drawer__updated">
                  Business refresh: {formatTimestamp(metadata.last_updated)}
                </div>
              )}
            </div>
          ) : null}
          {!d && (
            <p className="muted">Tap any evidence chip or KPI source line to inspect the lineage.</p>
          )}
        </div>
      </aside>
    </>
  );
}
