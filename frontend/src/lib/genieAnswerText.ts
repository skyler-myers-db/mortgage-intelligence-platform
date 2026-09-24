import type { GenieAnswer as GenieAnswerShape } from '../types';
import { normalizeGenieAnswerLanguage } from './genieAnswerLanguage';
import type { PinnedInsight } from './pinnedInsights';

/**
 * Genie answer text: the pin builder and the follow-up fallback, moved out of
 * lib/pinnedInsights.ts (audit 2026-09-21 `stack-02`). pinnedInsights.ts is in
 * the INITIAL closure (AppShell -> actorScopedBrowserState -> the pin store's
 * reset), so everything that only a rendered Genie answer needs lives here and
 * loads with the Genie answer chunk instead of on first paint. Import this
 * module only from Genie answer files.
 */

const MAX_SUMMARY = 220;

/**
 * Flatten the tiny Genie markdown vocabulary (`**bold**`, `` `code` ``,
 * leading bullets) to plain text and collapse whitespace/newlines. The pinned
 * card is a single-line hero on Home — it renders text, not markdown — so an
 * un-stripped summary would show literal "**" / "`" and mid-sentence line
 * breaks. Mirrors the inline grammar of MarkdownAnswer (GenieAnswer.markdown).
 */
function toPlainSummary(s: string): string {
  return normalizeGenieAnswerLanguage(s)
    .replace(/`([^`]+?)`/g, '$1') // inline code
    .replace(/\*\*([^*]+?)\*\*/g, '$1') // bold
    .replace(/^\s*[-*•]\s+/gm, '') // leading bullet markers
    .replace(/\s+/g, ' ') // collapse newlines/runs to single spaces
    .trim();
}

/** Truncate to a word boundary with an ellipsis, never mid-token, and trim any
 *  dangling separator/open-bracket left at the cut (e.g. a stray "(" or ","). */
function truncateAtWord(s: string, max: number): string {
  if (s.length <= max) return s;
  const slice = s.slice(0, max);
  const lastSpace = slice.lastIndexOf(' ');
  // Prefer the last word boundary, but only if it keeps a reasonable amount of
  // text (avoid collapsing to almost nothing when there are no early spaces).
  const base = lastSpace > max * 0.5 ? slice.slice(0, lastSpace) : slice;
  return `${base.replace(/[\s,;:.\-–—([{/&]+$/, '')}…`;
}

/** Derive a stable, summarized pin from a Genie answer payload + the question
 *  that produced it (the question lives in the conversation, not the payload). */
export function buildPinFromAnswer(
  payload: GenieAnswerShape,
  cleanedAnswer: string,
  questionText: string,
): PinnedInsight {
  const question = questionText.trim() || 'Genie insight';
  const id =
    payload.question_hash?.trim() ||
    payload.message_id?.trim() ||
    `q:${question.toLowerCase().replace(/\s+/g, ' ').slice(0, 80)}`;
  const summaryRaw =
    payload.metric_value != null && String(payload.metric_value).trim().length > 0
      ? String(payload.metric_value)
      : cleanedAnswer || (payload.answer ?? '');
  const summary = truncateAtWord(toPlainSummary(summaryRaw), MAX_SUMMARY) || 'Answered.';
  const source = Array.isArray(payload.trusted_assets) && payload.trusted_assets.length > 0
    ? payload.trusted_assets[0]
    : payload.source ?? null;
  return { id, question, summary, source, pinnedAt: new Date().toISOString() };
}

/**
 * Deterministic follow-up FALLBACK (Buyer-Wow #9). The Genie payload usually
 * carries follow_up_questions; when it doesn't, offer two context-aware
 * pivots derived from the answer shape so the loop never dead-ends. Never
 * fabricates data — these are just suggested next questions.
 */
export function buildFallbackFollowUps(payload: GenieAnswerShape): string[] {
  const hasRows = Array.isArray(payload.table_rows) && payload.table_rows.length > 0;
  const hasMetric = payload.metric_value != null && String(payload.metric_value).trim().length > 0;
  const out: string[] = [];
  if (hasMetric || hasRows) out.push('Break this down by state.');
  if (hasRows) out.push('Show the top cohorts.');
  else out.push('Which segments drive this?');
  return out.slice(0, 2);
}
