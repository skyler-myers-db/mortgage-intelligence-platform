import type { GenieActionResult, GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../../types';
import { ApiError, api } from '../../lib/api';
import { clearGenieConversationState } from '../../lib/genieConversation';

/**
 * Pure helpers of the Genie surfaces, shared by `GenieChat`, its transcript
 * body (`GenieChatBody`) and `/ask-genie`. Moved out of `GenieChat.tsx` so the
 * body module can use them without importing the panel that mounts it;
 * `GenieChat.tsx` re-exports them for existing callers.
 */

/** One trust boundary for both surfaces: lives with the in-flight turn store. */
export { shouldPersistConversation } from '../../lib/genieTurnOutcome';

export function sourceAssetsFor(payload: GenieAnswerShape): string[] {
  const seen = new Set<string>();
  const assets = [
    ...(payload.proof?.source_assets ?? []),
    ...(payload.trusted_assets ?? []),
  ];
  for (const raw of assets) {
    const asset = typeof raw === 'string' ? raw.trim() : '';
    if (asset) seen.add(asset);
  }
  return Array.from(seen).slice(0, 4);
}

export function warningLabelForSource(source: string | undefined): string | null {
  if (source === 'degraded') return 'Genie reconnecting';
  if (source === 'policy_blocked' || source === 'refused') return 'Governed refusal';
  if (source === 'data_gap') return 'Pending source feed';
  if (source === 'out_of_footprint') return 'Outside footprint';
  return null;
}

export function shouldRenderGenieSourceAssets(payload: GenieAnswerShape): boolean {
  return warningLabelForSource(payload.source) === null && sourceAssetsFor(payload).length > 0;
}

/** A governed action's result, tagged so each surface renders it its own way. */
export type GenieActionOutcome =
  | { kind: 'ok'; result: GenieActionResult }
  | { kind: 'failed'; message: string }
  | { kind: 'forbidden'; message: string };

/**
 * Run one governed action against the answer it was offered on (audit
 * 2026-09-21 `runtime-03`: the try/catch/finally that stopped the React
 * Compiler from compiling GenieChat and /ask-genie lives here, outside every
 * component). Pessimistic: the outcome is known only once the server's audit
 * write has returned. A 403 clears the conversation and tells every surface
 * (fail-closed identity boundary); the caller still shows the failure.
 */
export function runGenieActionRequest(
  action: GenieActionSuggestion,
  payload: GenieAnswerShape,
  conversationId: string | null,
): Promise<GenieActionOutcome> {
  return api
    .genieAction({
      ...action,
      conversation_id: payload.conversation_id ?? conversationId,
      message_id: payload.message_id ?? null,
      question_hash: payload.question_hash ?? null,
    })
    .then((result): GenieActionOutcome =>
      result.ok ? { kind: 'ok', result } : { kind: 'failed', message: `Action failed: ${result.message}` },
    )
    .catch((err: unknown): GenieActionOutcome => {
      const message = err instanceof Error ? `Action failed: ${err.message}` : 'Action failed.';
      if (err instanceof ApiError && err.status === 403) {
        clearGenieConversationState({ notify: true });
        return { kind: 'forbidden', message };
      }
      return { kind: 'failed', message };
    });
}

/** The confirmation line of a successful governed action. */
export function genieActionConfirmation(result: GenieActionResult): string {
  return result.audit_event_id ? `${result.message} Audit event ${result.audit_event_id}.` : result.message;
}
