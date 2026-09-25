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

/* ------------------------------------------------------------------------
 * The Genie answer markdown grammar (audit 2026-09-21 `stack-02`/`genie-07`).
 *
 * ONE grammar, read by two consumers: MarkdownAnswer renders it
 * (GenieAnswer.markdown.tsx) and flattenGenieMarkdown turns it into plain
 * text for the pin, the collapsed-turn digest and Copy answer. Nothing here
 * produces HTML; React escapes every string the renderer emits.
 *
 * Block constructs, one line at a time:
 *   - ordered items  `1. ` / `1) ` with a 1-3 digit number, so `2026. ...`
 *                    and `3.5%` stay prose;
 *   - bullets        `- `, `* `, `• `;
 *   - ATX headings   `# ` to `###### `, a closing `###` run stripped;
 *   - pipe rows      two or more consecutive `|...|` lines are a NARRATIVE
 *                    table: shown verbatim, never as a styled <table> (a
 *                    table of LLM-written numbers would read as governed
 *                    rows; critic fix 15);
 *   - fences         triple-backtick blocks, shown verbatim;
 *   - rules          a line of only 3+ `-`, `*` or `_` ends a block.
 * Inline constructs: `**bold**`, `` `code` ``, `*italic*` (guarded: never
 * next to `*` or a word character, no space inside the markers, and NEVER
 * `_`, which would mangle snake_case and UC names such as
 * mip.gold.lead_scores), and `[text](url)` / `![alt](url)` reduced to their
 * text: an LLM-authored URL never becomes a link and is never fetched.
 * ---------------------------------------------------------------------- */

export const GENIE_MD_ORDERED_RE = /^\s*(\d{1,3})[.)]\s+(.*)$/;
export const GENIE_MD_BULLET_RE = /^\s*([-*•])\s+(.*)$/;
export const GENIE_MD_HEADING_RE = /^\s{0,3}#{1,6}\s+(.*?)(?:\s+#+)?\s*$/;
export const GENIE_MD_FENCE_RE = /^\s*```/;
export const GENIE_MD_RULE_RE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const PIPE_ROW_RE = /^\s*\|.*\|\s*$/;
/** Bold, code, then a link or image (its text or alt in group 3). */
const INLINE_RE = /\*\*([^*]+?)\*\*|`([^`]+?)`|!?\[([^\]\n]*)\]\([^)\s]*(?:\s+"[^"\n]*")?\)/g;
/** Guarded italics: group 1 is the guard character, group 2 the text. */
const ITALIC_RE = /(^|[^*\w])\*([^\s*](?:[^*\n]*[^\s*])?)\*(?![*\w])/g;

export type GenieMdBlock =
  | { type: 'p'; text: string }
  | { type: 'ul'; items: string[] }
  | { type: 'ol'; start: number; items: string[] }
  | { type: 'h'; text: string }
  | { type: 'pre'; lines: string[]; narrative: boolean };

type ParsedBlock = GenieMdBlock | { type: 'rule' };

function isPipeRow(line: string | undefined): boolean {
  return line !== undefined && PIPE_ROW_RE.test(line.trimEnd());
}

/** A pipe row that belongs to a run of two or more (a lone `|x|` is prose). */
function inPipeRun(lines: readonly string[], index: number): boolean {
  return isPipeRow(lines[index]) && (isPipeRow(lines[index - 1]) || isPipeRow(lines[index + 1]));
}

/**
 * Split an answer into blocks. Consecutive prose lines join into one
 * paragraph (a blank line separates paragraphs); every other construct ends
 * the paragraph before it.
 */
export function parseGenieMarkdownBlocks(text: string): GenieMdBlock[] {
  const lines = text.split(/\r?\n/);
  const blocks: ParsedBlock[] = [];
  const last = (): ParsedBlock | undefined => blocks[blocks.length - 1];
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trimEnd();
    if (GENIE_MD_FENCE_RE.test(line)) {
      const body: string[] = [];
      for (i += 1; i < lines.length && !GENIE_MD_FENCE_RE.test(lines[i]); i += 1) body.push(lines[i].trimEnd());
      blocks.push({ type: 'pre', lines: body, narrative: false });
      continue;
    }
    if (inPipeRun(lines, i)) {
      const prev = last();
      if (prev?.type === 'pre' && prev.narrative && isPipeRow(lines[i - 1])) prev.lines.push(line.trim());
      else blocks.push({ type: 'pre', lines: [line.trim()], narrative: true });
      continue;
    }
    if (GENIE_MD_RULE_RE.test(line)) {
      blocks.push({ type: 'rule' });
      continue;
    }
    const heading = GENIE_MD_HEADING_RE.exec(line);
    if (heading) {
      blocks.push(heading[1] ? { type: 'h', text: heading[1] } : { type: 'rule' });
      continue;
    }
    const ordered = GENIE_MD_ORDERED_RE.exec(line);
    if (ordered) {
      const prev = last();
      if (prev?.type === 'ol') prev.items.push(ordered[2]);
      else blocks.push({ type: 'ol', start: Number(ordered[1]), items: [ordered[2]] });
      continue;
    }
    const bullet = GENIE_MD_BULLET_RE.exec(line);
    if (bullet) {
      const prev = last();
      if (prev?.type === 'ul') prev.items.push(bullet[2]);
      else blocks.push({ type: 'ul', items: [bullet[2]] });
      continue;
    }
    const prev = last();
    if (line.trim() === '') {
      if (prev?.type === 'p') blocks.push({ type: 'p', text: '' });
    } else if (prev?.type === 'p' && prev.text === '') {
      prev.text = line;
    } else if (prev?.type === 'p') {
      prev.text = `${prev.text} ${line.trim()}`;
    } else {
      blocks.push({ type: 'p', text: line });
    }
  }
  return blocks.filter((block): block is GenieMdBlock => {
    if (block.type === 'rule') return false;
    if (block.type === 'p') return block.text.length > 0;
    if (block.type === 'ul' || block.type === 'ol') return block.items.length > 0;
    return true;
  });
}

export type GenieInlineToken = { kind: 'text' | 'strong' | 'code' | 'em' | 'link'; text: string };

function pushPlain(tokens: GenieInlineToken[], segment: string): void {
  const re = new RegExp(ITALIC_RE.source, 'g');
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(segment)) !== null) {
    const start = match.index + match[1].length;
    if (start > last) tokens.push({ kind: 'text', text: segment.slice(last, start) });
    tokens.push({ kind: 'em', text: match[2] });
    last = re.lastIndex;
  }
  if (last < segment.length) tokens.push({ kind: 'text', text: segment.slice(last) });
}

/** Inline tokens of one block's text: bold, code and links first, italics after. */
export function tokenizeGenieInline(text: string): GenieInlineToken[] {
  const tokens: GenieInlineToken[] = [];
  const re = new RegExp(INLINE_RE.source, 'g');
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) pushPlain(tokens, text.slice(last, match.index));
    if (match[1] !== undefined) tokens.push({ kind: 'strong', text: match[1] });
    else if (match[2] !== undefined) tokens.push({ kind: 'code', text: match[2] });
    else tokens.push({ kind: 'link', text: match[3] ?? '' });
    last = re.lastIndex;
  }
  if (last < text.length) pushPlain(tokens, text.slice(last));
  return tokens;
}

function flattenInline(text: string): string {
  return tokenizeGenieInline(text).map((token) => token.text).join('');
}

/** A narrative-table row as words: cells joined, the `|---|` rule dropped. */
function pipeRowWords(line: string): string {
  const cells = line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
  return cells.every((cell) => /^:?-+:?$/.test(cell)) ? '' : cells.filter(Boolean).join(' ');
}

export type GenieFlattenMode = 'lines' | 'single';

/**
 * The answer as plain text, through the same grammar MarkdownAnswer renders.
 * `lines` keeps the line structure (Copy answer): bullets become `- `,
 * ordered items keep `N. `, narrative-table rows stay verbatim. `single` is
 * one line (the pin, the collapsed-turn digest): bullet markers drop, table
 * rows become their words and whitespace collapses. Fence and rule lines are
 * dropped in both modes; a fence's body stays as written.
 */
export function flattenGenieMarkdown(text: string, mode: GenieFlattenMode): string {
  const lines = text.split(/\r?\n/);
  const out: string[] = [];
  let inFence = false;
  lines.forEach((raw, index) => {
    const line = raw.trimEnd();
    if (GENIE_MD_FENCE_RE.test(line)) {
      inFence = !inFence;
      return;
    }
    if (inFence) {
      out.push(line);
      return;
    }
    if (inPipeRun(lines, index)) {
      out.push(mode === 'lines' ? line.trim() : pipeRowWords(line));
      return;
    }
    if (GENIE_MD_RULE_RE.test(line)) return;
    const heading = GENIE_MD_HEADING_RE.exec(line);
    if (heading) {
      out.push(flattenInline(heading[1]));
      return;
    }
    const ordered = GENIE_MD_ORDERED_RE.exec(line);
    if (ordered) {
      out.push(`${ordered[1]}. ${flattenInline(ordered[2])}`);
      return;
    }
    const bullet = GENIE_MD_BULLET_RE.exec(line);
    if (bullet) {
      out.push(mode === 'lines' ? `- ${flattenInline(bullet[2])}` : flattenInline(bullet[2]));
      return;
    }
    out.push(flattenInline(line));
  });
  const joined = out.join('\n');
  return mode === 'single' ? joined.replace(/\s+/g, ' ').trim() : joined.replace(/[ \t]+\n/g, '\n').trim();
}

/**
 * The pin summary as one plain line. The pinned card is a single-line hero on
 * Home -- it renders text, not markdown -- so an un-flattened summary would
 * show literal "**" / "`" and mid-sentence line breaks. Same grammar as
 * MarkdownAnswer, through flattenGenieMarkdown.
 */
function toPlainSummary(s: string): string {
  return flattenGenieMarkdown(normalizeGenieAnswerLanguage(s), 'single');
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
 * The one-line digest a collapsed earlier turn shows (audit `genie-08`): the
 * headline metric, else the executive summary, else the displayed answer,
 * flattened and cut at a word boundary. Presentation only; never stored.
 */
export function answerDigest(payload: GenieAnswerShape, cleanedAnswer: string, max = 160): string {
  const metric = payload.metric_value != null ? String(payload.metric_value).trim() : '';
  const raw = metric || (payload.summary ?? '').trim() || cleanedAnswer || (payload.answer ?? '');
  return truncateAtWord(toPlainSummary(raw), max);
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
