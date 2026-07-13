import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type {
  GenieActionSuggestion,
  GenieAnswer as GenieAnswerShape,
} from '../../types';
import { Icon } from '../Icon';
import { useApp } from '../AppContext';
import { GenieActions } from './GenieAnswerActions';
import {
  GenieBarChart,
  GenieBorrowerList,
  GenieLineChart,
  GenieMapChart,
  GenieStrategyBoard,
} from './GenieAnswerCharts';
import { MarkdownAnswer, stripQuestionRestatement } from './GenieAnswer.markdown';
import { normalizeGenieAnswerLanguage } from '../../lib/genieAnswerLanguage';
import { GenieProofPanel } from './GenieAnswerProof';
import { GenieAnswerFeedback } from './GenieAnswerFeedback';
import {
  buildFallbackFollowUps,
  buildPinFromAnswer,
  isTrustedGenieSource,
  usePinnedInsights,
} from '../../lib/pinnedInsights';
import {
  formatCell,
  humanizeKey,
  isIdentifierColumn,
  MAX_TABLE_COLS,
  MAX_TABLE_ROWS,
  pickPlan,
} from './GenieAnswer.logic';
import { useFocusTrap } from '../../hooks/useFocusTrap';

export { stripQuestionRestatement } from './GenieAnswer.markdown';
export { inferChartFromRows } from './GenieAnswer.logic';

/**
 * GenieAnswer — renders the widened Genie payload: metric_value (big tabular
 * number), answer paragraph, table_rows (compact 3-4 col table, truncated to
 * 10), and follow_up_questions (clickable .filter chips). Used inside both
 * the floating GenieChat bubble and the Ask Genie deep-dive route, so the
 * presenter experience is identical regardless of entry point.
 *
 * 2026-05-04 polish (user feedback "Genie output is weird"):
 *   - The Genie space frequently restates the question as the first
 *     sentence ("You want to see which ZIPs..."). That's filler the
 *     reader already knows — `stripQuestionRestatement` drops it.
 *   - Bold (**x**), inline code (`x`), and bullet lists ("- item")
 *     are rendered as real markup so emphasized ZIP codes / segment
 *     names actually pop instead of reading as raw asterisks.
 *   - Charts: native visualization metadata is surfaced only when the
 *     Conversation API returns it; structured rows remain the authoritative
 *     source for the app's interactive chart layer.
 */

interface GenieAnswerProps {
  payload: GenieAnswerShape;
  onFollowUp?: (q: string) => void;
  onAction?: (action: GenieActionSuggestion) => void | Promise<void>;
  /** The question that produced this answer (lives in the conversation, not
   *  the payload). When present on a genuine answer, enables "Pin to Home". */
  question?: string;
  /** Compact mode (used inside the floating chat bubble). */
  dense?: boolean;
  /** Render an inline chart when the table_rows shape is chartable.
   *  Off by default so the floating GenieChat bubble stays compact;
   *  the Ask Genie deep-dive route opts in. (FIX Δ3, 2026-05-04). */
  withChart?: boolean;
}

export function GenieAnswer({
  payload,
  onFollowUp,
  onAction,
  question,
  dense = false,
  withChart = false,
}: GenieAnswerProps) {
  const { answer, metric_value, table_rows, follow_up_questions, actions } = payload;
  const { setDrawer } = useApp();
  const { pins, pin, unpin } = usePinnedInsights();
  const [showProof, setShowProof] = useState(false);
  const proofDrawerRef = useRef<HTMLElement | null>(null);
  const proofCloseRef = useRef<HTMLButtonElement | null>(null);
  const rows = Array.isArray(table_rows) ? table_rows : [];
  const visibleRows = rows.slice(0, MAX_TABLE_ROWS);
  const hiddenRows = Math.max(0, rows.length - MAX_TABLE_ROWS);
  const columns = visibleRows[0] ? Object.keys(visibleRows[0]).slice(0, MAX_TABLE_COLS) : [];
  const chartColumns = rows[0] ? Object.keys(rows[0]) : [];
  const cleanedAnswer = answer ? normalizeGenieAnswerLanguage(stripQuestionRestatement(answer)) : '';
  const isGenieApiAnswer = payload.source === 'genie';
  // Live Genie identifiers are duplicated into proof so the governed audit
  // binding survives canonical answer rewrites. Prefer the top-level wire
  // fields and consume the proof copies when an older response shape omits
  // them; never synthesize an identifier client-side.
  const liveConversationId = payload.conversation_id ?? payload.proof?.conversation_id;
  const liveMessageId = payload.message_id ?? payload.proof?.message_id;
  const hasApiReasoning = Boolean(
    liveConversationId
    && liveMessageId
    && payload.genie_status === 'COMPLETED'
    && Array.isArray(payload.reasoning_trace)
    && payload.reasoning_trace.length > 0,
  );
  const reasoningSummaries = hasApiReasoning && Array.isArray(payload.reasoning_trace)
    ? payload.reasoning_trace
    : [];
  // "Pin to Home" (Buyer-Wow #9): only a genuine, trusted data answer is
  // pinnable — never a degraded/policy-blocked caveat. Trust is the app's
  // denylist (`isTrustedGenieSource`), so canonical `trusted_sql`/`sales_ops`
  // answers (the booth demo set) are pinnable too, not just `genie`. The
  // question comes from the conversation (the payload has no question field).
  const pinnable =
    Boolean(question) &&
    isTrustedGenieSource(payload.source) &&
    (Boolean(metric_value) || rows.length > 0 || cleanedAnswer.length > 0);
  const pinObject = pinnable ? buildPinFromAnswer(payload, cleanedAnswer, question!) : null;
  const isPinned = pinObject ? pins.some((p) => p.id === pinObject.id) : false;
  // Follow-up chips: Genie's own suggestions, or a deterministic fallback so
  // the loop never dead-ends. Only the SYNTHESIZED fallback is gated on the
  // trust boundary — never fabricate a "drill deeper" pivot on a governed
  // refusal or degraded answer (a made-up "Which segments drive this?" under a
  // fair-lending refusal reads as the product ignoring its own guardrail).
  // Explicit backend follow_up_questions are deliberately STILL honored on any
  // source: where the backend attaches them (e.g. warm-start `degraded`,
  // outreach `refused`) they are generic governed sample questions, not
  // refusal-referential pivots. Do not "tighten" this to drop them.
  const effectiveFollowUps =
    follow_up_questions && follow_up_questions.length > 0
      ? follow_up_questions
      : isTrustedGenieSource(payload.source)
        ? buildFallbackFollowUps(payload)
        : [];
  // Chart is optional and only computed from structured table_rows.
  // Answer prose is never parsed into visualization data.
  const plan = withChart ? pickPlan(payload, rows, chartColumns) : { kind: 'none', chart: null, viz: null };
  const chart = plan.chart;
  const proofToggle = payload.proof ? (
    <div className="genie-proof-toggle">
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => setShowProof((cur) => !cur)}>
        <Icon name="audit" size={12} />
        {showProof ? 'Hide proof' : 'Show proof'}
      </button>
      {payload.proof.trusted !== undefined && (
        <span className={payload.proof.trusted ? 'chip chip--success' : 'chip chip--warning'}>
          {payload.proof.trusted ? 'trusted' : 'review'}
        </span>
      )}
    </div>
  ) : null;
  // Native visualization marker. Genie generated a native chart attachment
  // for this answer, but in-app native rendering only activates once the
  // workspace enables the Beta download API. This is a NEUTRAL marker chip —
  // never a success/proven-capability tone — and we NEVER render a chart from
  // it. The heuristic table/chart layer above/below is unchanged.
  const nativeVizBadge = payload.native_visualization ? (
    <div className="genie-answer__native-viz">
      <span
        className="chip chip--neutral genie-answer__native-viz-chip"
        title="Genie generated a native visualization for this answer; in-app native rendering activates when the workspace enables the Beta download API."
      >
        <Icon name="bolt" size={10} />
        <span className="chip__label">Native chart · Beta</span>
      </span>
    </div>
  ) : null;

  useFocusTrap({
    open: showProof,
    containerRef: proofDrawerRef,
    initialFocusRef: proofCloseRef,
    onClose: () => setShowProof(false),
  });

  return (
    <div>
      {(isGenieApiAnswer || hasApiReasoning) && (
        <div
          className="genie-answer__api-source"
          aria-label="Answer source: Databricks Genie Conversation API"
        >
          <Icon name="sparkle" size={12} />
          <span>
            {payload.source === 'trusted_sql'
              ? 'Databricks Genie Conversation API · verified SQL'
              : 'Databricks Genie Conversation API'}
          </span>
        </div>
      )}
      {metric_value !== null && metric_value !== undefined && metric_value !== '' && (
        <div
          className={`genie-answer__metric ${dense ? 'genie-answer__metric--dense' : ''}`}
        >
          {metric_value}
        </div>
      )}
      {proofToggle}
      {nativeVizBadge}
      {cleanedAnswer && <MarkdownAnswer text={cleanedAnswer} />}
      {/* Databricks exposes bounded, PII-scrubbed planning summaries for live
          Genie turns. Render them as escaped text in a native disclosure; do
          not describe them as private or hidden chain-of-thought. */}
      {reasoningSummaries.length > 0 && (
        <details className="genie-answer__reasoning">
          <summary className="genie-answer__reasoning-summary">
            <Icon name="flow" size={12} />
            <span>API reasoning summary</span>
          </summary>
          <div
            className="genie-answer__reasoning-body"
            role="list"
            aria-label="Databricks Genie API reasoning summaries"
          >
            {reasoningSummaries.map((step, i) => (
              <div
                key={`${step.kind}-${i}`}
                className="genie-answer__reasoning-step"
                role="listitem"
              >
                <div className="genie-answer__reasoning-kind">
                  {humanizeKey(step.kind.replace(/^THOUGHT_TYPE_/, '').toLowerCase())}
                </div>
                <div className="genie-answer__reasoning-content">{step.content}</div>
              </div>
            ))}
          </div>
        </details>
      )}
      {/* FIX Δ3: chart renders BEFORE the underlying table so the user
          sees the visual summary first; the table stays as the
          authoritative data source below. Only renders when withChart
          is true (Ask Genie deep-dive route only) AND the data shape
          is chartable (1 categorical + 1 numeric column). */}
      {plan.kind === 'strategy_board' && (
        <GenieStrategyBoard rows={rows} x={plan.viz?.x} y={plan.viz?.y} />
      )}
      {plan.kind === 'borrower_list' && <GenieBorrowerList rows={rows} />}
      {plan.kind === 'map' && (
        <GenieMapChart rows={rows} x={plan.viz?.x ?? chart?.labelCol} y={plan.viz?.y ?? chart?.valueCol} />
      )}
      {plan.kind === 'line' && chart && (
        <GenieLineChart data={chart.rows} labelCol={chart.labelCol} valueCol={chart.valueCol} />
      )}
      {(plan.kind === 'bar' || plan.kind === 'funnel' || (!['strategy_board', 'borrower_list', 'map', 'line'].includes(plan.kind) && chart)) && chart && (
        <GenieBarChart
          data={chart.rows}
          labelCol={chart.labelCol}
          valueCol={chart.valueCol}
        />
      )}
      {visibleRows.length > 0 && columns.length > 0 && (
        <>
          <table className="genie-answer__table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c}>{humanizeKey(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row, i) => (
                <tr key={i}>
                  {columns.map((c) => {
                    const v = row[c];
                    const isNum = typeof v === 'number' && !isIdentifierColumn(c);
                    return (
                      <td key={c} className={isNum ? 'num' : undefined}>
                        {formatCell(c, v)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {hiddenRows > 0 && (
            <div className="genie-answer__more">+{hiddenRows} more row{hiddenRows === 1 ? '' : 's'}</div>
          )}
        </>
      )}
      {payload.proof && showProof && typeof document !== 'undefined' && createPortal(
        <>
          <div
            className="drawer-scrim is-open"
            onClick={() => setShowProof(false)}
            aria-hidden="true"
          />
          <aside
            ref={proofDrawerRef}
            className="drawer genie-proof-drawer is-open"
            role="dialog"
            aria-modal="true"
            aria-label="Genie answer proof"
          >
            <div className="drawer__hdr">
              <div className="drawer__source-icon">
                <Icon name="audit" size={16} />
              </div>
              <div>
                <div className="drawer__title">Answer proof</div>
                <div className="drawer__subtitle">{payload.question_hash ?? payload.message_id ?? 'Genie result'}</div>
              </div>
              <button
                ref={proofCloseRef}
                className="drawer__close"
                onClick={() => setShowProof(false)}
                aria-label="Close Genie proof"
                type="button"
              >
                <Icon name="close" size={14} />
              </button>
            </div>
            <div className="drawer__body">
              <GenieProofPanel
                payload={payload}
                onOpenSource={(source) => {
                  setShowProof(false);
                  setDrawer(source);
                }}
              />
            </div>
          </aside>
        </>,
        document.body,
      )}
      {onFollowUp && effectiveFollowUps.length > 0 && (
        <div className="genie-answer__followups">
          {effectiveFollowUps.slice(0, 5).map((q) => (
            <button
              key={q}
              type="button"
              className="filter filter--question"
              onClick={() => onFollowUp(q)}
            >
              <span className="filter__label">Ask</span>
              <span className="filter__value filter__value--question">{q}</span>
            </button>
          ))}
        </div>
      )}
      {pinObject && (
        <div className="genie-answer__pin-row">
          <button
            type="button"
            className={`btn btn--sm ${isPinned ? 'btn--ghost' : ''} genie-answer__pin`}
            onClick={() => (isPinned ? unpin(pinObject.id) : pin(pinObject))}
            aria-pressed={isPinned}
            data-testid="pin-to-home"
          >
            <Icon name={isPinned ? 'check' : 'pin'} size={12} />
            {isPinned ? 'Pinned to Home' : 'Pin to Home'}
          </button>
        </div>
      )}
      {actions && actions.length > 0 && onAction && (
        <GenieActions actions={actions} onAction={onAction} />
      )}
      {/* Feedback only on genuine, trusted answers — a thumbs vote on a
          governed refusal or degraded caveat rates the guardrail, not the
          analysis. GenieAnswerFeedback additionally no-ops when the payload
          lacks a conversation_id / message_id to attribute the vote to. */}
      {isTrustedGenieSource(payload.source) && (
        <GenieAnswerFeedback
          conversationId={liveConversationId}
          messageId={liveMessageId}
        />
      )}
    </div>
  );
}
