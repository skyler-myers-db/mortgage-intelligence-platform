import type { RefObject } from 'react';
import type { GenieLiveProgress } from '../../lib/api';
import { drawerForAsset } from '../../lib/drawerSources';
import type { GenieChatMessage } from '../../lib/genieConversationStore';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { Chip, EvidenceChip } from '../Primitives';
import { GenieAnswer } from './GenieAnswer';
import {
  shouldRenderGenieSourceAssets,
  sourceAssetsFor,
  warningLabelForSource,
} from './GenieChat.helpers';
import { GenieProgress } from './GenieProgress';

/**
 * Transcript body of the floating Genie panel: settled bubbles, the pending
 * question, the progress card, and the empty-state starters. Moved verbatim
 * out of `GenieChat.tsx` (file-size gate) so the panel's conversational
 * controls (audit 2026-09-21 `genie-03`) have room to land; markup and class
 * names are unchanged.
 */

export interface GenieChatBodyProps {
  open: boolean;
  bodyRef: RefObject<HTMLDivElement | null>;
  lastAnswerRef: RefObject<HTMLDivElement | null>;
  messages: GenieChatMessage[];
  pendingQuestion: string | null;
  /** An ask OR a governed action is in flight. */
  typing: boolean;
  liveProgress: GenieLiveProgress | null;
  askStartedAt: number | null;
  busyReason: string | null;
  /** Starter prompts for the empty state. */
  starters: string[];
  onAsk: (question: string, followUpConversationId?: string | null) => void;
  /** Returns the action's promise: GenieActions awaits it for its busy state. */
  onAction: (action: GenieActionSuggestion, payload: GenieAnswerShape) => void | Promise<void>;
}

export function GenieChatBody({
  open,
  bodyRef,
  lastAnswerRef,
  messages: msgs,
  pendingQuestion,
  typing,
  liveProgress,
  askStartedAt,
  busyReason,
  starters,
  onAsk,
  onAction,
}: GenieChatBodyProps) {
  const lastAnswerIndex = msgs.reduce((last, m, i) => (m.who === 'ai' ? i : last), -1);
  return (
    <div className="genie__body" ref={bodyRef}>
      {msgs.map((m, i) =>
        m.who === 'user' ? (
          <div key={i} className="genie__msg genie__msg--user">{m.text}</div>
        ) : (
          <div
            key={i}
            ref={i === lastAnswerIndex ? lastAnswerRef : undefined}
            className="genie__msg genie__msg--ai"
          >
            <div className="bubble">
              <GenieAnswer
                payload={m.payload}
                question={(() => {
                  const prev = msgs[i - 1];
                  return prev && prev.who === 'user' ? prev.text : undefined;
                })()}
                onFollowUp={(q, followUpConversationId) => onAsk(q, followUpConversationId)}
                followUpDisabledReason={busyReason}
                announce={false}
                onAction={(action) => onAction(action, m.payload)}
                dense
              />
            </div>
            {/* Source chip row. The backend emits "genie" (live)
                or governed refusal/degraded source values. Warning
                chips never pretend to be data-bearing answers. */}
            {warningLabelForSource(m.payload.source) && (
              <div className="sources">
                <Chip
                  variant="warning"
                  icon="info"
                  title={
                    m.payload.source === 'degraded'
                      ? 'The Genie answer path is temporarily unavailable. Live answers will resume after health recovers.'
                      : 'This answer intentionally stopped before displaying a live result.'
                  }
                >
                  {warningLabelForSource(m.payload.source)}
                </Chip>
              </div>
            )}
            {shouldRenderGenieSourceAssets(m.payload) && (
              <div className="sources">
                {(m.sources && m.sources.length > 0 ? m.sources : sourceAssetsFor(m.payload)).map((s, j) => {
                  const drawer = drawerForAsset(s);
                  if (drawer === null) {
                    // Source string doesn't map to a specific drawer
                    // entry — render an inert neutral chip so the
                    // user can read the source label without being
                    // misled into the wrong drawer (the prior
                    // "default to NBO" routing was confusing per
                    // 2026-05-04 user feedback).
                    return (
                      <Chip key={j} variant="neutral" title={`Source: ${s}`}>
                        {s}
                      </Chip>
                    );
                  }
                  return (
                    <EvidenceChip key={j} source={drawer} title={`Source: ${s}`}>
                      {s}
                    </EvidenceChip>
                  );
                })}
              </div>
            )}
          </div>
        )
      )}
      {pendingQuestion && (
        <div className="genie__msg genie__msg--user">{pendingQuestion}</div>
      )}
      {typing && (
        <div className="genie__msg genie__msg--ai">
          <div className="bubble">
            <GenieProgress
              dense
              progress={liveProgress}
              startedAt={askStartedAt}
              announce={false}
              paused={!open}
            />
          </div>
        </div>
      )}
      {msgs.length === 0 && !typing && (
        <div className="genie-chat__samples">
          <div className="surface surface--inset">
            <div className="surface__body genie-empty">
              <div className="genie-empty__icon">
                <Icon name="sparkle" size={16} />
              </div>
              <div>
                <div className="genie-empty__title">Ask about your book — coverage, segments, borrowers, market shifts.</div>
                <p className="genie-empty__copy">
                  Data-bearing answers appear only after Genie returns trusted SQL, source assets, and proof.
                </p>
              </div>
            </div>
          </div>
          {starters.map((s) => (
            <button
              key={s}
              className="filter genie-chat__sample"
              onClick={() => onAsk(s, undefined)}
              type="button"
            >
              <Icon name="sparkle" size={11} /> {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
