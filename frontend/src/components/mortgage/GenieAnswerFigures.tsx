/**
 * Figures in Genie's answer prose that match one returned cell (audit
 * 2026-09-21 `genie-10` phase 2). deviation:genie-figure-highlight (the
 * prototype's AI bubble is plain text with source chips).
 *
 * GenieAnswer provides a GenieFigureScopes map ONLY for a final, trusted
 * answer whose proof lists its verified figures, keyed by the exact text each
 * MarkdownAnswer receives (the single answer, the sweep summary, each section
 * answer). MarkdownAnswer reads its own entry through useGenieFigures and
 * marks the qualifying tokens (lib/genieFigureTokens decides, fail closed).
 * Without a provider or an entry nothing changes: the Partial research
 * preview, withheld prose, the factual row digest, a governed-action receipt,
 * a rescue answer and history recorded before the provenance fields render
 * exactly as before.
 *
 * The mark is a PASSIVE preview: it shows the evidence hover card on pointer
 * hover only and is not focusable (no tabindex). Keyboard and screen-reader
 * users read the same fact in the proof drawer's Verified figures list. A
 * hover preloads the evidence drawer's body chunk only, never data.
 */
import { createContext, useContext, type ReactNode } from 'react';
import type { GenieAnswer as GenieAnswerShape, GenieVerifiedClaim } from '../../types';
import type { DrawerSource } from '../AppContext';
import { useEvidenceHoverCard } from '../EvidenceHoverCard';
import {
  FIGURE_TOKEN_RE,
  MAX_FIGURE_TOKEN_CHARS,
  codePointPrefix,
  highlightableFigures,
  trimFigure,
  type FigureHighlight,
} from '../../lib/genieFigureTokens';
import { humanizeKey } from './GenieAnswer.logic';
import './GenieAnswerFigures.css';

/** Verified figures per rendered text: the provider's value. */
export type GenieFigureScopeMap = ReadonlyMap<string, readonly GenieVerifiedClaim[]>;

/** Figures to mark in one MarkdownAnswer, keyed by token. */
export type GenieFigureMarks = ReadonlyMap<string, FigureHighlight>;

export const GenieFigureScopes = createContext<GenieFigureScopeMap | null>(null);

export const FIGURE_HOVER_TITLE = 'Matches a returned value';
export const FIGURE_HOVER_CTA = 'Listed under Verified figures in the proof';

/** Python's `text[:n]` on a section title (the server's `[:200]` label). */
function sectionLabel(title: string): string {
  return codePointPrefix(title, 200);
}

/**
 * The provider's map for a trusted answer: the single answer's text with its
 * section-less items, or the sweep summary's with those and each section
 * answer's with the items carrying its (200-character) title. A text that
 * occurs twice, or two sections that share a title, are dropped (never
 * marked). `singleText` is the single-turn text MarkdownAnswer receives; it
 * is ignored when the answer has sections. Null when nothing qualifies.
 */
export function genieFigureScopes(payload: GenieAnswerShape, singleText: string): GenieFigureScopeMap | null {
  const items = payload.proof?.claims?.items ?? [];
  if (items.length === 0) return null;
  const sections = Array.isArray(payload.sections) ? payload.sections : [];
  const entries: Array<[string, GenieVerifiedClaim[]]> = [];
  const unscoped = items.filter((item) => !item.section);
  if (sections.length === 0) {
    entries.push([singleText, unscoped]);
  } else {
    entries.push([(payload.summary ?? '').trim(), unscoped]);
    const labels = sections.map((section) => sectionLabel(section.title));
    for (const [index, section] of sections.entries()) {
      const label = labels[index];
      const shared = labels.filter((other) => other === label).length > 1;
      entries.push([section.answer, shared ? [] : items.filter((item) => item.section === label)]);
    }
  }
  const seen = new Map<string, number>();
  for (const [text] of entries) seen.set(text, (seen.get(text) ?? 0) + 1);
  const scopes = new Map<string, GenieVerifiedClaim[]>();
  for (const [text, scoped] of entries) {
    if (text && seen.get(text) === 1 && scoped.length > 0) scopes.set(text, scoped);
  }
  return scopes.size > 0 ? scopes : null;
}

/** The marks for one MarkdownAnswer `text`; null without a provider or an entry. */
export function useGenieFigures(text: string, workspaceHost?: string | null): GenieFigureMarks | null {
  const scopes = useContext(GenieFigureScopes);
  const items = scopes?.get(text);
  if (!items) return null;
  const marks = highlightableFigures(text, items, workspaceHost);
  return marks.size > 0 ? marks : null;
}

/**
 * One text segment with its qualifying figures wrapped. Returns the segment
 * itself when nothing is marked, so an unmarked render is unchanged. Only
 * the trimmed token is wrapped; whitespace the match carried stays outside.
 */
export function markFigures(segment: string, figures: GenieFigureMarks | null): ReactNode {
  if (!figures) return segment;
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of segment.matchAll(FIGURE_TOKEN_RE)) {
    const token = trimFigure(match[0]);
    const figure = figures.get(token);
    if (!figure || codePointPrefix(token, MAX_FIGURE_TOKEN_CHARS) !== token) continue;
    if (match.index > last) out.push(segment.slice(last, match.index));
    out.push(<GenieFigureMark key={`figure-${match.index}`} token={token} column={figure.column} />);
    last = match.index + token.length;
  }
  if (out.length === 0) return segment;
  if (last < segment.length) out.push(segment.slice(last));
  return out;
}

function GenieFigureMark({ token, column }: { token: string; column: string | null }) {
  const source: DrawerSource = {
    title: FIGURE_HOVER_TITLE,
    signals: column ? [{ label: 'Column', source: 'Returned rows', value: humanizeKey(column) }] : undefined,
  };
  const { anchorRef, anchorHandlers, hoverCard } = useEvidenceHoverCard(source, { cta: FIGURE_HOVER_CTA });
  const { onMouseEnter, onMouseLeave } = anchorHandlers;
  // Pointer only, attached as native mouseenter/mouseleave listeners: the
  // mark stays a non-interactive element (no focus, no key handlers, no
  // role), because the same fact is in the proof drawer's Verified figures
  // list for keyboard and screen-reader users.
  const markRef = (el: HTMLElement | null) => {
    anchorRef(el);
    if (!el) return undefined;
    el.addEventListener('mouseenter', onMouseEnter);
    el.addEventListener('mouseleave', onMouseLeave);
    return () => {
      el.removeEventListener('mouseenter', onMouseEnter);
      el.removeEventListener('mouseleave', onMouseLeave);
      anchorRef(null);
    };
  };
  return (
    <>
      <mark ref={markRef} className="genie-md-figure">
        {token}
      </mark>
      {hoverCard}
    </>
  );
}
