import { useQuery } from '@tanstack/react-query';
import { assetsApi } from '../../lib/apiClients/assets';
import type { AssetFreshnessResponse } from '../../lib/apiTypes';
import { queryKeys } from '../../lib/queryKeys';
import { Timestamp } from '../ui/Timestamp';

/**
 * The evidence drawer's source summary: the freshness chip, the readiness
 * status, the destination, the explanation and one refresh fact (audit
 * 2026-09-21 `critic-03`, decision D-audit-reads-c1).
 *
 * Freshness comes from GET /api/assets/{key}/freshness for EVERY session,
 * administrators included: one reviewed source-readiness row, audit-free,
 * asked only while the drawer is open (never on hover, prefetch, idle or
 * poll) and never retried on its own. A failed read says it could not be
 * read; it never renders as "Stale" or "Freshness unavailable".
 *
 * deviation:evidence-freshness-basis — the prototype's DataSourceDrawer foot
 * reads "Last refresh: {updatedAt} · via Delta Share"
 * (design_files/Module 0 Prototype.html:1291); each source names the
 * reviewed readiness row its refresh comes from instead of the fixed
 * transport, for every role.
 */

const FIVE_MINUTES_MS = 5 * 60_000;

type FreshnessView =
  | AssetFreshnessResponse['freshness']
  | 'loading'
  | 'error'
  | 'not-tracked'
  | 'no-row';

const LABELS: Record<FreshnessView, string> = {
  loading: 'Checking freshness…',
  error: 'Freshness not loaded',
  'not-tracked': 'Freshness not tracked',
  'no-row': 'Freshness unavailable',
  fresh: 'Fresh',
  aging: 'Aging',
  stale: 'Stale',
  unavailable: 'Freshness unavailable',
};

const HELP: Record<FreshnessView, string> = {
  loading: 'Reading the source-readiness record.',
  error: 'Freshness could not be read just now. This does not mean the source is stale.',
  'not-tracked': 'This asset has no source-readiness row; its freshness is not tracked.',
  'no-row': 'No source-readiness row is available for this asset yet.',
  // Band edges: within 7 days, 7-30 days, past 30 days (asset_metadata_utils.freshness_bucket).
  fresh: 'Updated within 7 days.',
  aging: 'Updated 7-30 days ago.',
  stale: 'Updated more than 30 days ago.',
  unavailable: 'No backend refresh timestamp is available for this source.',
};

/** The chip modifier: 'not-tracked' and 'no-row' keep the base grey dot. */
function modifier(view: FreshnessView): string {
  return view === 'no-row' ? 'unavailable' : view;
}

interface EvidenceFreshnessProps {
  assetKey: string | undefined;
  open: boolean;
  /** Unity Catalog and readiness destinations carry freshness; Lakebase records do not. */
  tracked: boolean;
  destinationLabel: string;
  description: string | undefined;
}

export function EvidenceFreshness({ assetKey, open, tracked, destinationLabel, description }: EvidenceFreshnessProps) {
  const query = useQuery({
    queryKey: queryKeys.assetFreshness(assetKey),
    queryFn: ({ signal }) => assetsApi.assetFreshness(assetKey ?? '', signal),
    enabled: open && tracked && !!assetKey,
    staleTime: FIVE_MINUTES_MS,
    retry: false,
  });
  const data = query.data;
  const view: FreshnessView = !assetKey
    ? 'unavailable'
    : query.isError
      ? 'error'
      : !data
        ? 'loading'
        : data.source === 'not_tracked'
          ? 'not-tracked'
          : data.source === 'unavailable'
            ? 'no-row'
            : data.freshness;
  const status = data && data.status !== 'unknown' ? data.status : null;
  const lastRefresh = data?.source === 'source_readiness' ? data.last_updated : null;
  return (
    <div className="source-summary">
      <div className="source-summary__top">
        {tracked && (
          <span className={`source-freshness source-freshness--${modifier(view)}`} data-freshness-view={view}>
            {LABELS[view]}
          </span>
        )}
        {tracked && status && <span className="chip chip--neutral">{status}</span>}
        <span className="chip chip--neutral">{destinationLabel}</span>
      </div>
      <p className="body flush">{description}</p>
      {tracked && (
        <p className="muted fs-12 flush">
          {HELP[view]}
          {view === 'error' && (
            <>
              {' '}
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => void query.refetch()}>
                Retry
              </button>
            </>
          )}
        </p>
      )}
      {tracked && lastRefresh && data?.basis && (
        <div className="drawer__updated" data-testid="evidence-freshness-foot">
          Last refresh <Timestamp value={lastRefresh} format="relative" /> · via {data.basis}
        </div>
      )}
    </div>
  );
}
