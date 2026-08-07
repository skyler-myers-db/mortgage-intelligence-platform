import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { drawerForAsset } from '../../lib/drawerSources';
import { formatTimestamp } from '../../lib/time';
import type { DrawerSource } from '../AppContext';
import { Chip, EvidenceChip } from '../Primitives';
import { Icon } from '../Icon';
import { catalogExplorerUrl } from '../../lib/ucAssetLinks';

export function GenieProofPanel({
  payload,
  onOpenSource,
  workspaceHost,
}: {
  payload: GenieAnswerShape;
  onOpenSource: (source: DrawerSource) => void;
  /** Workspace origin for Catalog Explorer deep links. null/undefined ⇒ the
   *  asset chips render exactly as before (no external link affordance). */
  workspaceHost?: string | null;
}) {
  const proof = payload.proof;
  if (!proof) return null;
  const assets = proof.source_assets ?? payload.trusted_assets ?? [];
  return (
    <div className="genie-proof" role="region" aria-label="Genie proof">
      <div className="genie-proof__grid">
        <div className="genie-proof__metric">
          <div className="eyebrow">Trust</div>
          <div className={`genie-proof__trust-chip chip ${proof.trusted ? 'chip--success' : 'chip--warning'}`}>
            {proof.trusted ? 'Trusted SELECT on curated assets' : 'Review required'}
          </div>
        </div>
        <div className="genie-proof__metric">
          <div className="eyebrow">Rows</div>
          <div className="genie-proof__value">{proof.row_count ?? payload.row_count ?? 0}</div>
        </div>
        <div className="genie-proof__metric">
          <div className="eyebrow">Latency</div>
          <div className="genie-proof__value">
            {proof.elapsed_ms !== null && proof.elapsed_ms !== undefined ? `${proof.elapsed_ms} ms` : '—'}
          </div>
        </div>
      </div>
      {assets.length > 0 && (
        <div className="genie-proof__section">
          <div className="eyebrow">Source UC assets</div>
          <div className="chip-row">
            {assets.map((asset) => {
              const drawer = drawerForAsset(asset);
              const explorerUrl = catalogExplorerUrl(workspaceHost, asset);
              const explorerLink = explorerUrl ? (
                <a
                  className="chip chip--neutral uc-asset-link"
                  href={explorerUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`Open ${asset} in Catalog Explorer`}
                  title={`Open ${asset} in Databricks Catalog Explorer`}
                >
                  {/* Drawer-mapped assets keep the in-app evidence chip as the
                      primary action; this is the escape hatch to the workspace.
                      Unmapped assets get the asset name on the link itself so
                      the previously inert chip becomes reachable. */}
                  {!drawer && <span className="chip__label">{asset}</span>}
                  <Icon name="export" size={10} />
                </a>
              ) : null;
              return (
                <span key={asset} className="genie-proof__asset">
                  {drawer ? (
                    <EvidenceChip source={drawer} onClick={() => onOpenSource(drawer)}>
                      {asset}
                    </EvidenceChip>
                  ) : (
                    !explorerLink && (
                      <Chip variant="neutral" title={`Source: ${asset}`}>
                        {asset}
                      </Chip>
                    )
                  )}
                  {explorerLink}
                </span>
              );
            })}
          </div>
        </div>
      )}
      {proof.data_freshness && proof.data_freshness.length > 0 && (
        <div className="genie-proof__section">
          <div className="eyebrow">Data freshness</div>
          {proof.data_freshness.map((f) => (
            <div key={`${f.asset}-${f.refreshed_at ?? f.status}`} className="genie-proof__line">
              <span>{f.asset}</span>
              <span>{f.refreshed_at ? formatTimestamp(f.refreshed_at) : f.status}</span>
            </div>
          ))}
        </div>
      )}
      {proof.filters && proof.filters.length > 0 && (
        <div className="genie-proof__section">
          <div className="eyebrow">Filters applied</div>
          {proof.filters.map((filter) => <code key={filter} className="genie-proof__sql">{filter}</code>)}
        </div>
      )}
      {proof.known_data_gaps && proof.known_data_gaps.length > 0 && (
        <div className="genie-proof__section">
          <div className="eyebrow">Known data gaps</div>
          {proof.known_data_gaps.map((gap) => <div key={gap} className="genie-proof__gap">{gap}</div>)}
        </div>
      )}
      {proof.sql_query && (
        <div className="genie-proof__section">
          <div className="eyebrow">Generated SQL</div>
          <pre className="genie-proof__sql">{proof.sql_query}</pre>
        </div>
      )}
    </div>
  );
}
