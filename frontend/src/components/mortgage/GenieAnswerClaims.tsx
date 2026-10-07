/**
 * Figures verified against the returned rows (audit 2026-09-21 `genie-10`),
 * in the proof drawer. deviation:genie-figures-verified (the prototype's
 * proof shows source chips only).
 *
 * The server's claims verifier already checks every figure in Genie's prose
 * against the rows before the prose may ship; this makes that check visible:
 * an "N of M answer figures verified" metric and, per figure, its kind and
 * how the rows support it (the first 40; the list says so when the metric
 * counts more). Nothing renders when the answer carries no claims (withheld
 * prose, refusals, history recorded before the field) or checked none. An
 * unsupported figure is never listed: its prose was withheld instead.
 *
 * Owner ruling O2 (2026-10-06): the metric counts the answer's OWN figures,
 * those in Genie's governed prose. A deterministic cross-check note is the
 * disclosure layer, not the answer: the server checks the prose before the
 * note is appended, so the note's figures are never counted, and the note
 * stays visible as its own disclosure in the answer and in Known data gaps.
 *
 * Phase 2: an item that carries its provenance (`support`) says which kind
 * of returned value proves it, naming the column where there is exactly
 * one; an older item falls back to its derivation.
 */
import type { GenieClaimsSummary, GenieVerifiedClaim } from '../../types';
import { humanizeKey } from './GenieAnswer.logic';
import './GenieAnswerClaims.css';

const KIND_LABELS: Record<GenieVerifiedClaim['kind'], string> = {
  currency: 'Amount',
  percent: 'Percent',
  bps: 'Basis points',
  number: 'Count or value',
};

const DERIVATION_LABELS: Record<GenieVerifiedClaim['derivation'], string> = {
  returned_value: 'Matches a returned value',
  derived_from_rows: 'Derived from the returned rows (a total, average, share or change)',
  bound: 'A threshold the returned values satisfy',
};

/** How the returned rows prove one figure: its provenance when the server
 *  sent one, else today's derivation label (bound and older answers). */
function provenanceLabel(item: GenieVerifiedClaim): string {
  const column = item.column ? humanizeKey(item.column) : null;
  switch (item.support) {
    case 'cell':
      return column ? `Matches a returned value in ${column}` : 'Matches a returned value';
    case 'row_count':
      return 'The number of returned rows';
    case 'sum':
      return column ? `The total of the returned ${column} values` : 'A total of a returned column';
    case 'share':
      return column ? `A share of the returned ${column} total` : 'A share of a returned column total';
    case 'derived':
      return 'Derived from the returned rows (an average, range, change or running total)';
    default:
      return DERIVATION_LABELS[item.derivation];
  }
}

/** Claims worth showing: present and at least one figure checked. */
export function shownClaims(claims: GenieClaimsSummary | null | undefined): GenieClaimsSummary | null {
  return claims && claims.total > 0 ? claims : null;
}

/** The proof grid's "Figures" metric. */
export function GenieClaimsMetric({ claims }: { claims: GenieClaimsSummary }) {
  return (
    <div className="genie-proof__metric">
      <div className="eyebrow">Figures</div>
      <div className="genie-proof__value">
        {claims.verified} of {claims.total} answer {claims.total === 1 ? 'figure' : 'figures'} verified
      </div>
    </div>
  );
}

function groups(items: readonly GenieVerifiedClaim[]): Array<[string | null, GenieVerifiedClaim[]]> {
  const sweep = items.some((item) => item.section);
  const order: Array<[string | null, GenieVerifiedClaim[]]> = [];
  for (const item of items) {
    const title = sweep ? (item.section ?? 'Summary') : null;
    const group = order.find(([name]) => name === title);
    if (group) group[1].push(item);
    else order.push([title, [item]]);
  }
  return order;
}

/** "Verified figures": each figure, its kind and its derivation, grouped
 *  under "Summary" and each section title on a deep-research answer. */
export function GenieClaimsList({ claims }: { claims: GenieClaimsSummary }) {
  if (claims.items.length === 0) return null;
  return (
    <div className="genie-proof__section genie-claims">
      <div className="eyebrow">Verified figures</div>
      {groups(claims.items).map(([title, items]) => (
        <div key={title ?? 'answer'} className="genie-claims__group">
          {title && <div className="genie-claims__group-title">{title}</div>}
          <ul className="genie-claims__list">
            {items.map((item, index) => (
              <li key={`${item.token}-${index}`} className="genie-claims__item">
                <span className="genie-claims__token mono">{item.token}</span>
                <span className="genie-claims__kind">{KIND_LABELS[item.kind]}</span>
                <span className="genie-claims__derivation">{provenanceLabel(item)}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {claims.items.length < claims.verified && (
        // The server lists at most 40 figures (GenieClaimsSummary.items) while
        // the metric counts every verified one; say the list is a prefix.
        <div className="genie-claims__more">
          The first {claims.items.length} of {claims.verified} verified answer figures are listed.
        </div>
      )}
    </div>
  );
}
