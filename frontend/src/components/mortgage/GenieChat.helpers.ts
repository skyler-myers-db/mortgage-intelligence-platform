import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { NON_PERSISTABLE_SOURCES } from '../../lib/pinnedInsights';

/**
 * Pure helpers of the floating Genie panel, shared by `GenieChat` and its
 * transcript body (`GenieChatBody`). Moved verbatim out of `GenieChat.tsx`
 * so the body module can use them without importing the panel that mounts
 * it; `GenieChat.tsx` re-exports them for existing callers.
 */

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

export function shouldPersistConversation(payload: GenieAnswerShape): boolean {
  return Boolean(payload.conversation_id && !NON_PERSISTABLE_SOURCES.has(String(payload.source ?? '')));
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
