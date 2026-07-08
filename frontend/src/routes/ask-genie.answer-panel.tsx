import type { RefObject } from 'react';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../types';
import type { WarmingUpState } from '../lib/useWarmingUpRetry';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { GenieAnswer } from '../components/mortgage/GenieAnswer';
import { GenieProgress } from '../components/mortgage/GenieProgress';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { drawerForAsset } from '../lib/drawerSources';

/**
 * AskGenieAnswerPanel — the composer + answer surface extracted from
 * `ask-genie.tsx` (props in, callbacks out; mirrors the
 * ask-genie.compose-plan-card / ask-genie.growth-run-card precedent).
 *
 * Behavior is unchanged from the inlined version. The source-chip
 * classification depends only on `payload`, so it lives here rather than in
 * the parent — it moved wholesale with the surface it annotates.
 */

export interface AskGenieAnswerPanelProps {
  questionRef: RefObject<HTMLTextAreaElement | null>;
  question: string;
  /** Called on textarea change with the raw value (parent clears active asset). */
  onQuestionChange: (value: string) => void;
  /** Commit the current question to the warming-up fetch. */
  onAsk: (question: string) => void;
  /** Start a fresh Genie thread. */
  onNewThread: () => void;
  loading: boolean;
  warmingUp: WarmingUpState | null;
  errorMsg: string | null;
  onRetry: () => void;
  /** Full sample-question list; the composer shows the first four. */
  sampleQuestions: string[];
  payload: GenieAnswerShape | null;
  submittedQuestion: string | null;
  onFollowUp: (question: string) => void;
  onAction: (action: GenieActionSuggestion) => void;
  actionStatus: string | null;
}

export function AskGenieAnswerPanel({
  questionRef,
  question,
  onQuestionChange,
  onAsk,
  onNewThread,
  loading,
  warmingUp,
  errorMsg,
  onRetry,
  sampleQuestions,
  payload,
  submittedQuestion,
  onFollowUp,
  onAction,
  actionStatus,
}: AskGenieAnswerPanelProps) {
  const sourceLabel = payload?.source ?? '';
  // The backend emits "genie" for live answers, or governed refusal/degraded
  // source values when it intentionally stops before showing data.
  const isDegraded = sourceLabel === 'degraded';
  const isBlocked =
    sourceLabel === 'policy_blocked' ||
    sourceLabel === 'refused' ||
    sourceLabel === 'data_gap' ||
    sourceLabel === 'out_of_footprint';
  const sourceChip = isDegraded
    ? 'Genie reconnecting'
    : isBlocked
      ? sourceLabel === 'refused'
        ? 'Prompt refused'
        : sourceLabel === 'data_gap'
          ? 'Source pending'
          : sourceLabel === 'out_of_footprint'
            ? 'Outside footprint'
          : 'Policy blocked'
      : payload?.trusted_assets?.[0] || sourceLabel || '';
  const sourceChipTitle = isDegraded
    ? 'The Genie answer path is temporarily unavailable. Live answers will resume after health recovers.'
    : isBlocked
      ? 'The answer was not displayed because it did not meet the governed Genie policy.'
    : undefined;
  const sourceChipVariant: 'warning' | undefined = isDegraded || isBlocked ? 'warning' : undefined;
  const drawerForSource = sourceChip ? drawerForAsset(sourceChip) : null;
  const composerSampleQuestions = sampleQuestions.slice(0, 4);

  return (
    <div className="surface">
      <div className="surface__hdr">
        <Icon name="sparkle" size={14} className="icon-accent" />
        <div className="h-4">Ask a question</div>
      </div>
      <div className="surface__body">
        <textarea
          ref={questionRef}
          aria-label="Ask Genie — question"
          value={question}
          onChange={(e) => {
            onQuestionChange(e.target.value);
          }}
          onKeyDown={(e) => {
            // 2026-05-04 (FIX Δ1): standard chat keymap — Enter
            // submits, Shift+Enter inserts a newline. Match how
            // Slack / GitHub PRs behave so the keyboard-first user
            // doesn't have to mouse over to the Ask Genie button.
            // The submit-disabled guard mirrors the button's
            // `disabled` prop so a stray Enter during a warming-up
            // request can't double-fire.
            if (
              e.key === 'Enter' &&
              !e.shiftKey &&
              !e.metaKey &&
              !e.ctrlKey &&
              !e.altKey
            ) {
              e.preventDefault();
              if (!loading && warmingUp === null && question.trim().length > 0) {
                onAsk(question);
              }
            }
          }}
          className="route-textarea route-textarea--genie"
        />
        {composerSampleQuestions.length > 0 && (
          <div className="genie-composer__samples" aria-label="Suggested Genie questions">
            {composerSampleQuestions.map((q) => (
              <button
                key={q}
                type="button"
                className="filter filter--question"
                onClick={() => onAsk(q)}
              >
                <Icon name="sparkle" size={11} />
                <span className="filter__text">{q}</span>
              </button>
            ))}
          </div>
        )}
        <div className="section-actions">
          <Button
            variant="primary"
            icon="send"
            onClick={() => onAsk(question)}
            disabled={loading || warmingUp !== null || question.trim().length === 0}
          >
            {loading || warmingUp !== null ? 'Asking…' : 'Ask Genie'}
          </Button>
          <Button
            variant="ghost"
            icon="chat"
            onClick={onNewThread}
            disabled={loading || warmingUp !== null}
          >
            New thread
          </Button>
        </div>
        {warmingUp && (
          <div className="mt-4">
            <WarmingUpBlock state={warmingUp} title="Asking Genie" compact />
          </div>
        )}
        {loading && !warmingUp && (
          <div className="surface surface--inset mt-4">
            <div className="surface__body">
              <GenieProgress />
            </div>
          </div>
        )}
        {errorMsg && !warmingUp && (
          <div
            className="surface surface--inset surface--danger mt-4"
            role="alert"
          >
            <div className="surface__body status-callout--danger">
              <span>{errorMsg}</span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={onRetry}
                disabled={loading}
                aria-label="Retry Genie question"
              >
                Retry
              </button>
            </div>
          </div>
        )}
        {!payload && !loading && !warmingUp && !errorMsg && (
          <div className="surface surface--inset mt-4">
            <div className="surface__body genie-empty">
              <div className="genie-empty__icon">
                <Icon name="sparkle" size={16} />
              </div>
              <div>
                <div className="genie-empty__title">Ready for governed analysis</div>
                <p className="genie-empty__copy">
                  Trusted SQL, source assets, freshness, and approval-safe actions appear with each answer.
                </p>
              </div>
            </div>
          </div>
        )}
        {payload && (
          <div
            className="surface surface--inset mt-4"
          >
            <div className="surface__body">
              {sourceChip && (
                <div className="chip-row mb-3">
                  <span className="muted fs-11">Source:</span>
                  {sourceChipVariant === 'warning' ? (
                    // Degraded: warning chip with tooltip so the user
                    // knows Genie is reconnecting. Not clickable.
                    <Chip
                      variant="warning"
                      icon="info"
                      title={sourceChipTitle}
                    >
                      {sourceChip}
                    </Chip>
                  ) : drawerForSource ? (
                    // Specific UC asset → open the matching drawer entry.
                    <EvidenceChip source={drawerForSource}>{sourceChip}</EvidenceChip>
                  ) : (
                    // Generic / unknown source → inert chip so a click
                    // doesn't open the wrong drawer. (Prior code
                    // defaulted to NBO and was misleading.)
                    <Chip variant="neutral" title={`Source: ${sourceChip}`}>
                      {sourceChip}
                    </Chip>
                  )}
                </div>
              )}
              {/* withChart=true: opt this deep-dive view in to the
                  auto-detected bar chart for top-N / per-state-style
                  table_rows payloads. The floating bubble does NOT
                  pass this prop, so its compact form is unchanged. */}
              <GenieAnswer payload={payload} question={submittedQuestion ?? undefined} onFollowUp={onFollowUp} onAction={onAction} withChart />
              {actionStatus && (
                <div className="status-callout status-callout--info mt-3">
                  {actionStatus}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
