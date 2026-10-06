import { useState } from 'react';
import { Icon } from '../Icon';
import type { KpiProofResponse } from '../../lib/apiTypes';

/**
 * The KPI reproduce card on the drawer's Under the hood tab (audit 2026-09-21
 * `flow-06` phase 2): the governed, parameter-bound, gold-only statement the
 * server emits for the number on the card (GET /api/kpi-proof), on the
 * borrower proof drawer's reproduce pattern and classes (.proof-callout,
 * .proof-sql-card; BorrowerProofDrawer.tsx). Nothing is composed here.
 *
 * deviation:evidence-under-the-hood — a third drawer tab carrying the
 * reproduce SQL and the catalog detail, beside the prototype's Overview /
 * Lineage drawer (design_files/index.html:665-681).
 */

/** The shared KPI proof query, as the drawer body holds it (one GET per KPI per document). */
export interface KpiProofQuery {
  data: KpiProofResponse | undefined;
  isError: boolean;
  refetch: () => unknown;
}

const COPIED_MS = 1600;

export function EvidenceKpiProof({ title, proof }: { title: string; proof: KpiProofQuery }) {
  const [copied, setCopied] = useState(false);
  const data = proof.data;
  if (proof.isError) {
    return (
      <div className="source-card source-card--warning" role="status">
        The reproduce SQL could not be loaded.{' '}
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => void proof.refetch()}>
          Retry
        </button>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="source-card" role="status" aria-live="polite">
        Loading the reproduce SQL…
      </div>
    );
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(data.sql);
      setCopied(true);
      window.setTimeout(() => setCopied(false), COPIED_MS);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="stack-md" data-testid="evidence-kpi-proof">
      <div className="proof-callout">
        <p className="body flush">
          Copy this statement into a Databricks SQL workspace with grants on the listed objects; it is the statement
          that produced the number.
        </p>
      </div>
      <div className="proof-sql-card">
        <div className="split-row">
          <div>
            <div className="proof-sql-card__title">{title}</div>
            <div className="muted fs-12">{data.note}</div>
          </div>
          <span className="mono proof-sql-card__hash">{data.sql_hash}</span>
        </div>
        <pre className="proof-sql-card__sql">{data.sql}</pre>
        {data.params.length > 0 && (
          <>
            <div className="muted fs-12">Bound parameters</div>
            <pre className="proof-sql-card__sql">
              {data.params.map((param) => `:${param.name} = ${param.value}`).join('\n')}
            </pre>
          </>
        )}
        <div className="chip-row" role="group" aria-label="Governed objects read">
          {data.relations.map((relation) => (
            <span key={relation} className="chip chip--neutral mono">{relation}</span>
          ))}
        </div>
        <div className="chip-row mt-3">
          <button type="button" className="btn btn--sm" onClick={() => void copy()}>
            <Icon name={copied ? 'check' : 'doc'} size={12} />
            {copied ? 'Copied' : 'Copy SQL'}
          </button>
          {data.databricks_sql_url && (
            <a className="btn btn--sm" href={data.databricks_sql_url} target="_blank" rel="noreferrer">
              Open SQL editor
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
