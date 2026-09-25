/**
 * ProofMargins — "What would change this?" (wow-ai-1 phase 1). Ships in the
 * lazy Score anatomy chunk.
 *
 * BEM block `.score-margins` (ScoreSpine.css), a <dl>: each margin's label,
 * its deterministic value, a muted threshold line and an EvidenceChip for
 * the governed asset it came from. Neutral styling on purpose: no pass /
 * fail chips, nothing ordered as ranked causes. The note that this is
 * marketing prioritization and not a credit decision is always shown.
 *
 * The par margins quote the par on the dossier row, which moves only when
 * the gold refresh runs (the FRED ingest schedule ships paused), so the
 * provenance line dates it and never says "today's par". A payload without
 * margins (an older server) hides the section.
 */
import type { BorrowerProof, ProofMargin } from '../../types';
import { descriptorFor } from '../../lib/drawerSources';
import { formatTimestamp, parseBackendTimestamp } from '../../lib/time';
import { EvidenceChip } from '../Primitives';
import { SCORE_SPINE_COPY } from './scoreSpine.copy';
import './ScoreSpine.css';

const PAR_KEYS: ReadonlySet<ProofMargin['key']> = new Set(['par_break_even', 'offer_flip_par']);

/**
 * When the par was materialized. The proof's `source_refresh_at` reads
 * "dossier <ts> / lead_scores <ts>"; the par sits on the dossier row.
 */
export function parRefreshLabel(sourceRefreshAt: string | null | undefined): string {
  if (!sourceRefreshAt) return SCORE_SPINE_COPY.parRefreshFallback;
  const dossier = /\bdossier\s+(\S+)/.exec(sourceRefreshAt)?.[1] ?? sourceRefreshAt;
  const parsed = parseBackendTimestamp(dossier);
  return parsed ? formatTimestamp(parsed) : SCORE_SPINE_COPY.parRefreshFallback;
}

/** "Par 6.26% as of <refresh> — rates move only when …", or null without a computed par margin. */
export function parProvenance(proof: BorrowerProof): string | null {
  const par = proof.margins?.find((margin) => PAR_KEYS.has(margin.key) && margin.direction !== 'unavailable');
  if (!par) return null;
  const quoted = par.threshold.charAt(0).toUpperCase() + par.threshold.slice(1);
  return `${quoted} as of ${parRefreshLabel(proof.source_refresh_at)} — ${SCORE_SPINE_COPY.parProvenanceTail}`;
}

/** `titled`: under the spine the section names itself; alone, the disclosure button already does. */
export function ProofMargins({ proof, titled = false }: { proof: BorrowerProof; titled?: boolean }) {
  const margins = proof.margins ?? [];
  if (margins.length === 0) return null;
  const provenance = parProvenance(proof);
  return (
    <div className="score-margins" data-testid="score-margins">
      {titled && <div className="eyebrow score-margins__title">{SCORE_SPINE_COPY.marginsTitle}</div>}
      <dl className="score-margins__list">
        {margins.map((margin) => {
          const source = margin.source ? descriptorFor(margin.source) : null;
          return (
            <div key={margin.key} className="score-margins__row" data-margin={margin.key} data-direction={margin.direction}>
              <dt className="score-margins__label">{margin.label}</dt>
              <dd className="score-margins__value">
                <span>{margin.value_text}</span>
                <span className="score-margins__threshold">{margin.threshold}</span>
                {source && <EvidenceChip source={source}>{source.title}</EvidenceChip>}
              </dd>
            </div>
          );
        })}
      </dl>
      {provenance && (
        <p className="score-margins__note" data-testid="score-margins-provenance">{provenance}</p>
      )}
      <p className="score-margins__note" data-testid="score-margins-note">{SCORE_SPINE_COPY.marginsNote}</p>
    </div>
  );
}
