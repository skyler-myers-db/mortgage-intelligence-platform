import type { GenieVerifiedClaim } from '../types';
import { parseGenieMarkdownBlocks, tokenizeGenieInline } from './genieAnswerText';
import { splitSourceLinks } from './genieSourceLinks';

/**
 * Figure tokens in Genie's answer prose (audit 2026-09-21 `genie-10` phase 2).
 *
 * The claims verifier proves every figure in the prose against the returned
 * rows and lists the verified ones by RAW TOKEN; no span, index or offset
 * ever crosses the wire. This module finds those tokens again in the text the
 * answer renders, so a figure that matches one returned cell can be marked.
 * Pure: no React, no fetch.
 *
 * FIGURE_TOKEN_RE mirrors the backend `_NUMBER_RE`
 * (backend/services/repositories/databricks_genie_numeric.py) exactly, and a
 * token is `match.strip()[:32]` on both sides. Two JS regex defaults differ
 * from Python's `re` on str patterns, so both are spelled out: `\d` is any
 * Unicode decimal digit (`\p{Nd}`), and `\s` is Python's whitespace set (JS
 * `\s` adds U+FEFF and lacks U+001C-U+001F and U+0085). The 32-character cap
 * counts code points, as Python's slice does. genieFigureTokens.parity.json
 * is asserted against both grammars (vitest and pytest).
 *
 * Highlighting fails closed: a token qualifies only when the server located
 * it (`support === 'cell'` with a numeric `occurrences`, set only when EVERY
 * number with that token in the checked prose is a cell-supported claim) AND
 * its count over the full text AND over the rendered segments both equal
 * `occurrences`. Anything else (an appended disclosure that repeats the
 * token, a stripped restatement, a list marker or a link target the renderer
 * drops, a disagreeing duplicate item) is never marked.
 */

/** Python's `\s` for str patterns (str.isspace), as a character-class body. */
const PY_SPACE = '\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';

export const FIGURE_TOKEN_RE = new RegExp(
  '(?<![A-Za-z0-9_-])'
    + '\\$?'
    + '(?:\\p{Nd}{1,3}(?:,\\p{Nd}{3})+|\\p{Nd}+)(?:\\.\\p{Nd}+)?'
    + `[${PY_SPACE}]*[kKmMbB]?`
    + '%?'
    + '(?![A-Za-z0-9_-])',
  'gu',
);

const PY_TRIM_RE = new RegExp(`^[${PY_SPACE}]+|[${PY_SPACE}]+$`, 'gu');

/** The longest token the server lists (GenieVerifiedClaim.token). */
export const MAX_FIGURE_TOKEN_CHARS = 32;

/** Python's `raw.strip()` with the same whitespace set. */
export function trimFigure(raw: string): string {
  return raw.replace(PY_TRIM_RE, '');
}

/** Python's `text[:n]`: a code-point prefix. */
export function codePointPrefix(text: string, n: number): string {
  return Array.from(text).slice(0, n).join('');
}

/** Every figure token in `text`, in order: `match.strip()[:32]`. */
export function figureTokens(text: string): string[] {
  return Array.from(text.matchAll(FIGURE_TOKEN_RE), (match) =>
    codePointPrefix(trimFigure(match[0]), MAX_FIGURE_TOKEN_CHARS),
  );
}

function countTokens(texts: readonly string[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const text of texts) {
    for (const token of figureTokens(text)) counts.set(token, (counts.get(token) ?? 0) + 1);
  }
  return counts;
}

/**
 * The text segments MarkdownAnswer renders, one string each: every inline
 * token of a paragraph, heading or list item, with a text token split exactly
 * where the "Source:" linker splits it (the link text is its own segment),
 * and each verbatim block as one segment. Code, link and verbatim segments
 * are counted but MarkdownAnswer never marks inside them.
 */
export function renderedSegments(text: string, workspaceHost?: string | null): string[] {
  const out: string[] = [];
  for (const block of parseGenieMarkdownBlocks(text)) {
    if (block.type === 'pre') {
      out.push(block.lines.join('\n'));
      continue;
    }
    const inline = block.type === 'ul' || block.type === 'ol' ? block.items : [block.text];
    for (const part of inline) {
      for (const token of tokenizeGenieInline(part)) {
        if (token.kind !== 'text') {
          out.push(token.text);
          continue;
        }
        for (const piece of splitSourceLinks(token.text, workspaceHost)) out.push(piece.text);
      }
    }
  }
  return out;
}

export interface FigureHighlight {
  /** The returned column the figure matches; null when the server named none
   *  or the items listing this token disagree on it. */
  column: string | null;
}

/**
 * Figures to mark in `text`, keyed by token. Fails closed on every
 * disagreement: an item without a cell support or a numeric occurrences
 * count disqualifies its token, as do items that disagree on the count, and
 * a token whose full-text or rendered-segment count differs from it.
 */
export function highlightableFigures(
  text: string,
  items: readonly GenieVerifiedClaim[],
  workspaceHost?: string | null,
): Map<string, FigureHighlight> {
  const byToken = new Map<string, { occurrences: number | null; columns: Set<string | null> }>();
  for (const item of items) {
    const occurrences = item.support === 'cell' && typeof item.occurrences === 'number' ? item.occurrences : null;
    const entry = byToken.get(item.token);
    if (!entry) {
      byToken.set(item.token, { occurrences, columns: new Set([item.column ?? null]) });
    } else {
      if (entry.occurrences !== occurrences) entry.occurrences = null;
      entry.columns.add(item.column ?? null);
    }
  }
  const out = new Map<string, FigureHighlight>();
  if (![...byToken.values()].some((entry) => entry.occurrences !== null)) return out;
  const full = countTokens([text]);
  const rendered = countTokens(renderedSegments(text, workspaceHost));
  for (const [token, entry] of byToken) {
    const n = entry.occurrences;
    if (n === null || n < 1 || full.get(token) !== n || rendered.get(token) !== n) continue;
    const [column] = entry.columns;
    out.set(token, { column: entry.columns.size === 1 ? column : null });
  }
  return out;
}
