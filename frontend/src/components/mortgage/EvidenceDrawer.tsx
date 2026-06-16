import { Fragment, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useApp } from '../AppContext';
import { Icon } from '../Icon';
import { api } from '../../lib/api';
import { assetDetailHref, assetHrefForSource } from '../../lib/drawerSources';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { queryKeys } from '../../lib/queryKeys';
import { formatTimestamp } from '../../lib/time';
import type { AssetFreshness, AssetMetadataResponse } from '../../types';

/**
 * Data source / evidence drawer — fast context for a source chip.
 * The drawer starts with human explanation and, when the source maps to a
 * trusted Module 0 asset, enriches itself with governed UC metadata.
 */

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
  const metadataQuery = useQuery({
    queryKey: queryKeys.assetMetadata(d?.assetKey),
    queryFn: ({ signal }) => api.assetMetadata(d?.assetKey ?? '', signal),
    enabled: open && !!d?.assetKey,
    retry: false,
  });
  const metadata = metadataQuery.data;
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
        aria-label="Data source and lineage"
        aria-hidden={!open}
      >
        <div className="drawer__hdr">
          <div className="drawer__source-icon">
            <Icon name="db" size={16} />
          </div>
          <div className="drawer__hdr-main">
            <div className="drawer__title">{d?.title ?? 'Data source'}</div>
            <div className="drawer__subtitle">{d?.short ?? d?.assetPath ?? 'Source proof'}</div>
          </div>
          <button ref={closeBtnRef} className="drawer__close" onClick={() => setDrawer(null)} aria-label="Close drawer" type="button">
            <Icon name="close" size={14} />
          </button>
        </div>
        <div className="drawer__body">
          {d ? (
            <>
              <div className="source-summary">
                <div className="source-summary__top">
                  {/* Modifier keys off the VIEW state, not metadata.freshness:
                      'loading' and 'error' previously fell through to the
                      --unavailable style, visually conflating "checking" /
                      "fetch failed" with "no timestamp" (re-audit 2026-06-11
                      cosmetic finding). */}
                  <span className={`source-freshness source-freshness--${freshnessView ?? 'unavailable'}`}>
                    {freshnessLabel(freshnessView)}
                  </span>
                  {metadata?.status && <span className="chip chip--neutral">{metadata.status}</span>}
                </div>
                <p className="body flush">{d.description}</p>
                <p className="muted fs-12 flush">{freshnessHelp(freshnessView)}</p>
              </div>

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

              {d.lineage && d.lineage.length > 0 && (
                <>
                  <div className="eyebrow mt-4 mb-2">Lineage</div>
                  {d.lineage.map((n, i) => (
                    <Fragment key={`${n.name}-${i}`}>
                      <div className="lineage-node">
                        <div className="lineage-node__label">{n.layer}</div>
                        <div className="lineage-node__name">{n.name}</div>
                        {n.meta && <div className="lineage-node__meta">{n.meta}</div>}
                      </div>
                      {d.lineage && i < d.lineage.length - 1 && <div className="lineage-arrow">↓</div>}
                    </Fragment>
                  ))}
                </>
              )}

              {metadata?.lineage && metadata.lineage.length > 0 && (
                <>
                  <div className="eyebrow mt-5 mb-2">Observed UC lineage</div>
                  {metadata.lineage.map((n) => {
                    const lineageHref = assetHrefForSource(n.asset_path);
                    if (!lineageHref) {
                      return (
                        <div key={`${n.direction}-${n.asset_path}`} className="lineage-node">
                          <div className="lineage-node__label">{n.direction}</div>
                          <div className="lineage-node__name">{n.label}</div>
                          {n.event_time && <div className="lineage-node__meta">{formatTimestamp(n.event_time, { withYear: false })}</div>}
                        </div>
                      );
                    }
                    return (
                      <Link
                        key={`${n.direction}-${n.asset_path}`}
                        to={lineageHref}
                        className="lineage-node lineage-node--link"
                        onClick={() => setDrawer(null)}
                      >
                        <div className="lineage-node__label">{n.direction}</div>
                        <div className="lineage-node__name">{n.label}</div>
                        {n.event_time && <div className="lineage-node__meta">{formatTimestamp(n.event_time, { withYear: false })}</div>}
                      </Link>
                    );
                  })}
                </>
              )}

              {d.signals && d.signals.length > 0 && (
                <>
                  <div className="eyebrow mt-5 mb-2">Sanitized signals</div>
                  {d.signals.map((s, i) => (
                    <div key={`${s.label}-${i}`} className="lineage-node lineage-node--signal">
                      <div>
                        <div className="lineage-node__label">{s.label}</div>
                        <div className="lineage-node__name">{s.source}</div>
                      </div>
                      <div className="mono num lineage-node__value">{s.value}</div>
                    </div>
                  ))}
                </>
              )}

              <div className="drawer__actions">
                {assetHref && (
                  <Link className="btn btn--primary btn--sm" to={assetHref} onClick={() => setDrawer(null)}>
                    <Icon name="db" size={12} />
                    View asset details
                  </Link>
                )}
                {metadata?.catalog_explorer_url && (
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
            </>
          ) : (
            <p className="muted">Tap any evidence chip or KPI source line to inspect the lineage.</p>
          )}
        </div>
      </aside>
    </>
  );
}
