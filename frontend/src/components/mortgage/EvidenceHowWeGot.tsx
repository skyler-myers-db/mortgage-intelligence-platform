import type { DrawerSource } from '../AppContext';
import { SurfaceTitle } from '../Primitives';
import { Timestamp } from '../ui/Timestamp';
import type { KpiProofQuery } from './EvidenceKpiProof';
import './EvidenceHowWeGot.css';

/**
 * "How we got {value}" (audit 2026-09-21 `flow-06` phase 1): the evidence
 * drawer leads with the number the user clicked, its one-sentence
 * definition, the filters behind it and when the data was refreshed, before
 * the catalog detail. Rendered only for a source that carries `value`.
 *
 * The filter chips are the source's own `predicates`, else, for a KPI with
 * server-emitted reproduce SQL, the predicates of that statement (one shared
 * GET per KPI per document, open-only), so a chip never claims a filter the
 * query did not apply.
 *
 * deviation:evidence-how-we-got — the prototype's DataSourceDrawer opens on
 * the source description and signals (design_files/Module 0 Prototype.html:
 * 1268-1291); a KPI's drawer now leads with the number, its definition, its
 * filters and its as-of. Block `.evidence-proof` (EvidenceHowWeGot.css).
 */

const WHOLE_BOOK = 'Whole refreshed book (no filter)';

function FilterChips({ source, proof }: { source: DrawerSource; proof: KpiProofQuery | null }) {
  let chips: readonly string[] | null = null;
  let status: string | null = null;
  if (source.predicates !== undefined) {
    chips = source.predicates;
  } else if (source.proofKey && proof) {
    if (proof.data) chips = proof.data.predicates;
    else if (proof.isError) status = 'Filters not loaded';
    else status = 'Loading filters…';
  }
  if (chips === null && status === null) return null;
  return (
    <div className="chip-row evidence-proof__filters" role="group" aria-label="Filters applied">
      {status !== null ? (
        <span className="muted fs-12" role="status">{status}</span>
      ) : chips && chips.length > 0 ? (
        chips.map((chip) => (
          <span key={chip} className="chip chip--neutral mono">{chip}</span>
        ))
      ) : (
        <span className="chip chip--neutral mono">{WHOLE_BOOK}</span>
      )}
    </div>
  );
}

export function EvidenceHowWeGot({ source, proof }: { source: DrawerSource; proof: KpiProofQuery | null }) {
  if (!source.value) return null;
  return (
    <section className="evidence-proof" aria-labelledby="evidence-proof-title" data-testid="evidence-how-we-got">
      <SurfaceTitle level={3} id="evidence-proof-title" className="evidence-proof__title">
        How we got {source.value}
      </SurfaceTitle>
      {source.definition && <p className="body flush">{source.definition}</p>}
      <FilterChips source={source} proof={proof} />
      {source.asOf && (
        <p className="muted fs-12 flush evidence-proof__asof">
          Data as of <Timestamp value={source.asOf} format="datetime" />
        </p>
      )}
    </section>
  );
}
