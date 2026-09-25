import { useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { formatTimestamp } from '../../lib/time';
import { formatFixed } from '../../lib/formatters';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import type { BorrowerProof, ProofFormulaLine, ProofReproduceQuery, ProofScoreComponentKey } from '../../types';
import { Button, Chip } from '../Primitives';
import { Icon } from '../Icon';
import { Skeleton } from '../ui/Skeleton';
import { GlossaryTerm } from '../GlossaryTerm';
import { useTabs } from '../ui/useTabs';
import { useBorrowerProof } from './useBorrowerProof';
// Same lazy chunk: ScoreAnatomy is the drawer's only importer.
import { SCORE_SPINE_COPY } from './scoreSpine.copy';
import { SurfaceTitle } from '../ui/SurfaceTitle';

type ProofTab = 'math' | 'evidence' | 'lineage' | 'reproduce';

interface BorrowerProofDrawerProps {
  borrowerId: string;
  open: boolean;
  onClose: () => void;
  /**
   * Opened from a Score anatomy segment: the drawer selects the Math tab and
   * moves focus to that component's card (wow-stage-2). "Show math" passes
   * nothing and focuses the close button, as before.
   */
  focusComponent?: ProofScoreComponentKey | null;
}

const TABS: Array<{ id: ProofTab; label: string; icon: 'audit' | 'layers' | 'flow' | 'db' }> = [
  { id: 'math', label: 'Math', icon: 'audit' },
  { id: 'evidence', label: 'Evidence', icon: 'layers' },
  { id: 'lineage', label: 'Lineage', icon: 'flow' },
  { id: 'reproduce', label: 'Reproduce', icon: 'db' },
];
const TAB_IDS: readonly ProofTab[] = TABS.map((item) => item.id);

function formatWeight(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formulaRows(proof: BorrowerProof): ProofFormulaLine[] {
  return [
    proof.score_formula,
    proof.signal_strength_formula,
    proof.rate_spread_formula,
    proof.equity_formula,
    proof.ltv_formula,
  ];
}

export function BorrowerProofDrawer({ borrowerId, open, onClose, focusComponent = null }: BorrowerProofDrawerProps) {
  const [tab, setTab] = useState<ProofTab>('math');
  // A segment-opened drawer lands on the Math tab, where its card lives.
  const [openLatch, setOpenLatch] = useState(open);
  if (openLatch !== open) {
    setOpenLatch(open);
    if (open && focusComponent) setTab('math');
  }
  // APG tabs (audit a11y-02): arrow keys, Home/End, roving tabindex, and the
  // body is the tabpanel the selected tab controls.
  const tabs = useTabs({ tabs: TAB_IDS, selected: tab, onSelect: setTab, idBase: 'proof-drawer' });
  const [copiedHash, setCopiedHash] = useState<string | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  const focusCardRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  // The one proof query (critic fix 16): opening the drawer is the explicit
  // act that may read; a re-open over a cached proof is a pure cache read,
  // even after an approve invalidated ['mip', 'borrower', ...].
  const proofQuery = useBorrowerProof(borrowerId, open);

  useFocusTrap<HTMLElement, HTMLElement>({
    open,
    containerRef: panelRef,
    initialFocusRef: focusComponent ? focusCardRef : closeBtnRef,
    onClose,
  });

  const proof = proofQuery.data;
  const copySql = async (query: ProofReproduceQuery) => {
    try {
      await navigator.clipboard.writeText(query.sql);
      setCopiedHash(query.sql_hash);
      window.setTimeout(() => setCopiedHash((cur) => (cur === query.sql_hash ? null : cur)), 1600);
    } catch {
      setCopiedHash(null);
    }
  };

  const drawer = (
    <>
      <div
        className={`drawer-scrim ${open ? 'is-open' : ''}`}
        onClick={onClose}
        aria-hidden={!open}
      />
      <aside
        ref={panelRef}
        className={`drawer proof-drawer ${open ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={`Proof for borrower ${borrowerId}`}
        aria-hidden={!open}
      >
        <div className="drawer__hdr">
          <div className="drawer__source-icon">
            <Icon name="shield" size={16} />
          </div>
          <div>
            <div className="drawer__title">Proof: Borrower {borrowerId}</div>
            <div className="drawer__subtitle">
              Explainable, traceable, reproducible scoring.
            </div>
          </div>
          <button ref={closeBtnRef} className="drawer__close" onClick={onClose} aria-label="Close proof drawer" type="button">
            <Icon name="close" size={14} />
          </button>
        </div>

        <div className="proof-tabs" {...tabs.tabListProps} aria-label="Proof sections">
          {TABS.map((item) => (
            <button
              key={item.id}
              {...tabs.tabProps(item.id)}
              className={`proof-tab ${tab === item.id ? 'is-active' : ''}`}
            >
              <Icon name={item.icon} size={12} />
              <span>{item.label}</span>
            </button>
          ))}
        </div>

        <div className="drawer__body proof-drawer__body" {...tabs.panelProps(tab)}>
          {proofQuery.isPending && (
            <div className="stack-md" aria-busy="true" role="status">
              <Skeleton width="62%" height={16} rounded="sm" />
              <Skeleton width="90%" height={12} rounded="sm" />
              <Skeleton width="80%" height={12} rounded="sm" />
              <Skeleton width="70%" height={12} rounded="sm" />
            </div>
          )}

          {/* The proof query is static (useBorrowerProof), so a health
              recovery never re-reads it: every read writes a
              VIEW_BORROWER_PROOF row, and only an explicit act may. "Try
              again" is that act, disabled while a read is in flight so a
              second click cannot cancel it and send another. */}
          {proofQuery.isError && (
            <div className="proof-callout proof-callout--warning">
              <SurfaceTitle>Proof unavailable</SurfaceTitle>
              <p className="body flush">
                The borrower dossier loaded, but the governed proof endpoint did not return. Try again
                once the warehouse is healthy.
              </p>
              <Button size="sm" className="mt-2" disabled={proofQuery.isFetching} onClick={() => void proofQuery.refetch()}>
                {SCORE_SPINE_COPY.retry}
              </Button>
            </div>
          )}

          {proof && (
            <>
              <div className="proof-status-row">
                <Chip variant={proof.trusted ? 'success' : 'warning'}>
                  {proof.trusted ? 'Governed proof ready' : 'Proof has gaps'}
                </Chip>
                <span className="mono num">{proof.opportunity_score} score</span>
                <span className="mono num">{proof.signal_strength}% signal</span>
              </div>
              {proof.known_data_gaps.length > 0 && (
                <div className="proof-callout proof-callout--warning">
                  <div className="eyebrow">Known data gaps</div>
                  <ul className="proof-list">
                    {proof.known_data_gaps.map((gap) => <li key={gap}>{gap}</li>)}
                  </ul>
                </div>
              )}

              {tab === 'math' && (
                <div className="stack-md">
                  <div className="proof-callout">
                    <div className="eyebrow">What this number means</div>
                    <p className="body flush">
                      <GlossaryTerm term="signalStrength" /> is deterministic scoring signal coverage,
                      not a statistical confidence interval or a credit decision probability.
                    </p>
                  </div>
                  <div className="proof-formula-grid">
                    {formulaRows(proof).map((formula) => (
                      <FormulaCard key={formula.label} formula={formula} />
                    ))}
                  </div>
                  <div className="eyebrow">Score components</div>
                  <div className="proof-components">
                    {proof.score_components.map((component) => {
                      const focused = component.key === focusComponent;
                      return (
                      <div
                        key={component.key}
                        ref={focused ? focusCardRef : undefined}
                        className={`proof-component${focused ? ' proof-component--focused' : ''}`}
                        tabIndex={focused ? -1 : undefined}
                        data-component-key={component.key}
                      >
                        <div className="proof-component__top">
                          <div>
                            <div className="proof-component__label">{component.label}</div>
                            <div className="muted fs-12">{component.explanation}</div>
                          </div>
                          <div className="proof-component__score mono num">
                            {component.value}
                          </div>
                        </div>
                        <div className="proof-component__bar" aria-hidden="true">
                          <span style={{ '--bar-pct': `${component.value}%` } as CSSProperties} />
                        </div>
                        <div className="proof-component__meta">
                          <span>{formatWeight(component.weight)} weight</span>
                          <span>{formatFixed(component.weighted_points, 2)} weighted points</span>
                        </div>
                        {component.source_fields.length > 0 && (
                          <div className="proof-component__fields">
                            {component.source_fields.map((field) => (
                              <span key={field} className="mono">{field}</span>
                            ))}
                          </div>
                        )}
                        {component.fair_lending_note && (
                          <div className="proof-callout proof-callout--subtle">
                            {component.fair_lending_note}
                          </div>
                        )}
                      </div>
                      );
                    })}
                  </div>
                  <div className="eyebrow">Primary offer branch</div>
                  <div className="proof-branches">
                    {proof.offer_branches.map((branch) => (
                      <div
                        key={branch.code}
                        className={`proof-branch ${branch.selected ? 'is-selected' : ''} ${branch.passed ? 'is-passed' : ''}`}
                      >
                        <div className="split-row">
                          <span className="proof-branch__label">
                            {offerDisplayLabel(branch.code, branch.label)}
                          </span>
                          <Chip variant={branch.selected ? 'success' : branch.passed ? 'neutral' : 'warning'}>
                            {branch.selected ? 'Selected' : branch.passed ? 'Passed' : 'Did not pass'}
                          </Chip>
                        </div>
                        <p className="muted fs-12 flush">{branch.reason}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {tab === 'evidence' && (
                <div className="stack-md">
                  <div className="proof-callout">
                    <p className="body flush">
                      <GlossaryTerm term="evidenceConfidence" /> is source-row confidence from
                      governed evidence events. It is separate from borrower signal strength.
                    </p>
                  </div>
                  {proof.evidence_rows.map((row) => (
                    <div key={row.evidence_id} className="proof-evidence-row">
                      <div>
                        <div className="proof-evidence-row__title">{row.source_product}</div>
                        <div className="muted fs-12">{row.display_text}</div>
                      </div>
                      <div className="proof-evidence-row__meta">
                        <span className="mono">{row.signal_type}</span>
                        <span className="mono num">{formatFixed(row.confidence, 3)} evidence confidence</span>
                        <span className="mono">{formatTimestamp(row.timestamp, { withYear: 'auto' })}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {tab === 'lineage' && (
                <div className="stack-md">
                  <div className="proof-callout">
                    <div className="eyebrow">Generated from</div>
                    <p className="body flush">{proof.generated_from}</p>
                    {proof.source_refresh_at && (
                      <p className="muted fs-12 flush">Source refresh: {formatTimestamp(proof.source_refresh_at)}</p>
                    )}
                  </div>
                  <div className="eyebrow">Governed Unity Catalog assets</div>
                  <div className="lineage-list">
                    {proof.source_assets.map((asset) => (
                      <div key={asset} className="lineage-node">
                        <div className="lineage-node__label">UC asset</div>
                        <div className="lineage-node__name">{asset}</div>
                      </div>
                    ))}
                  </div>
                  <div className="proof-callout proof-callout--subtle">
                    Reproduction runs against governed, masked gold tables. Raw identifiers and street addresses stay out of this view.
                  </div>
                </div>
              )}

              {tab === 'reproduce' && (
                <div className="stack-md">
                  <div className="proof-callout">
                    <p className="body flush">
                      Copy these fixed-template SQL queries into an authenticated Databricks SQL
                      workspace with grants on the listed assets. Replace
                      <span className="mono"> :borrower_id</span> with the masked borrower id shown
                      here; the query runs against governed, masked data.
                    </p>
                  </div>
                  {proof.reproduce.map((query) => (
                    <div key={query.sql_hash} className="proof-sql-card">
                      <div className="split-row">
                        <div>
                          <div className="proof-sql-card__title">{query.title}</div>
                          <div className="muted fs-12">{query.note}</div>
                        </div>
                        <span className="mono proof-sql-card__hash">{query.sql_hash}</span>
                      </div>
                      <pre className="proof-sql-card__sql">{query.sql}</pre>
                      <div className="chip-row">
                        <Button
                          size="sm"
                          icon={copiedHash === query.sql_hash ? 'check' : 'doc'}
                          onClick={() => void copySql(query)}
                        >
                          {copiedHash === query.sql_hash ? 'Copied' : 'Copy SQL'}
                        </Button>
                        {query.databricks_sql_url && (
                          <a
                            className="btn btn--sm"
                            href={query.databricks_sql_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Open SQL editor
                          </a>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );

  return createPortal(drawer, document.body);
}

function FormulaCard({ formula }: { formula: ProofFormulaLine }) {
  return (
    <div className="proof-formula">
      <div className="eyebrow">{formula.label}</div>
      <div className="proof-formula__expr mono">{formula.expression}</div>
      <div className="proof-formula__result">{formula.result}</div>
      {formula.source && (
        <div className="proof-formula__source mono">{formula.source}</div>
      )}
    </div>
  );
}
