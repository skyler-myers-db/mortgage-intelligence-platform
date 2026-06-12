import { useState } from 'react';
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
import { GenieProofPanel } from './GenieAnswerProof';
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
 *   - Charts: the Genie API does support attachment-based charts but
 *     the message endpoint returns them as separate attachment IDs
 *     that have to be fetched via a follow-up call. Not wired today;
 *     the follow-up question chips below let the user pivot to a
 *     more visual question (e.g. "show as a bar chart") and the
 *     table_rows path covers the most common "list me N things" ask.
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
  const rows = Array.isArray(table_rows) ? table_rows : [];
  const visibleRows = rows.slice(0, MAX_TABLE_ROWS);
  const hiddenRows = Math.max(0, rows.length - MAX_TABLE_ROWS);
  const columns = visibleRows[0] ? Object.keys(visibleRows[0]).slice(0, MAX_TABLE_COLS) : [];
  const chartColumns = rows[0] ? Object.keys(rows[0]) : [];
  const cleanedAnswer = answer ? stripQuestionRestatement(answer) : '';
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
  // the loop never dead-ends.
  const effectiveFollowUps =
    follow_up_questions && follow_up_questions.length > 0
      ? follow_up_questions
      : buildFallbackFollowUps(payload);
  // Chart is optional and only computed from structured table_rows.
  // Answer prose is never parsed into visualization data.
  const plan = withChart ? pickPlan(payload, rows, chartColumns) : { kind: 'none', chart: null, viz: null };
  const chart = plan.chart;

  return (
    <div>
      {metric_value !== null && metric_value !== undefined && metric_value !== '' && (
        <div
          className={`genie-answer__metric ${dense ? 'genie-answer__metric--dense' : ''}`}
        >
          {metric_value}
        </div>
      )}
      {cleanedAnswer && <MarkdownAnswer text={cleanedAnswer} />}
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
      {payload.proof && (
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
      )}
      {payload.proof && showProof && typeof document !== 'undefined' && createPortal(
        <>
          <div
            className="drawer-scrim is-open"
            onClick={() => setShowProof(false)}
            aria-hidden="true"
          />
          <aside
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
          {effectiveFollowUps.slice(0, 3).map((q) => (
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
    </div>
  );
}
