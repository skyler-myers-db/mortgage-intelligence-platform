import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { drawerForAsset } from '../../lib/drawerSources';
import { formatTimestamp } from '../../lib/time';
import type { DrawerSource } from '../AppContext';
import { Chip, EvidenceChip } from '../Primitives';
import { humanizeKey } from './GenieAnswer.logic';

export function GenieProofPanel({
  payload,
  onOpenSource,
}: {
  payload: GenieAnswerShape;
  onOpenSource: (source: DrawerSource) => void;
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
          <div className="genie-proof__value">{proof.elapsed_ms ? `${proof.elapsed_ms} ms` : '—'}</div>
        </div>
      </div>
      {assets.length > 0 && (
        <div className="genie-proof__section">
          <div className="eyebrow">Source UC assets</div>
          <div className="chip-row">
            {assets.map((asset) => {
              const drawer = drawerForAsset(asset);
              return drawer ? (
                <EvidenceChip key={asset} source={drawer} onClick={() => onOpenSource(drawer)}>
                  {asset}
                </EvidenceChip>
              ) : (
                <Chip key={asset} variant="neutral" title={`Source: ${asset}`}>
                  {asset}
                </Chip>
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
      {proof.reasoning_trace && proof.reasoning_trace.length > 0 && (
        <div className="genie-proof__section">
          <div className="eyebrow">Genie query trace</div>
          <div className="genie-proof__trace">
            {proof.reasoning_trace.slice(0, 4).map((step, i) => (
              <div key={`${step.kind}-${i}`} className="genie-proof__trace-step">
                <div className="genie-proof__trace-kind">
                  {humanizeKey(step.kind.replace(/^THOUGHT_TYPE_/, '').toLowerCase())}
                </div>
                <div className="genie-proof__trace-content">{step.content}</div>
              </div>
            ))}
          </div>
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
