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
 * The mark itself (GenieFigureMark, with its stylesheet) loads lazily the
 * first time an answer has a figure to mark; until then the token renders as
 * the same plain text, so the route closure carries only the decision code.
 * A chunk that fails to load keeps the plain text (never a route error).
 * It is a PASSIVE preview: the evidence hover card on pointer hover only, not
 * focusable; keyboard and screen-reader users read the same fact in the
 * proof drawer's Verified figures list.
 */
import { createContext, lazy, Suspense, useContext, type ComponentType, type ReactNode } from 'react';
import type { GenieAnswer as GenieAnswerShape, GenieVerifiedClaim } from '../../types';
import {
  FIGURE_TOKEN_RE,
  MAX_FIGURE_TOKEN_CHARS,
  codePointPrefix,
  highlightableFigures,
  trimFigure,
  type FigureHighlight,
} from '../../lib/genieFigureTokens';

type FigureMarkProps = { token: string; column: string | null };

/** The unmarked token: what the answer shows when the mark chunk cannot load. */
const PlainFigure: ComponentType<FigureMarkProps> = ({ token }) => <>{token}</>;

// The mark is decorative, so its chunk fails soft (as Rail.tsx's
// RailRoadmap): a tab opened before a redeploy, or a network blip, renders
// the plain token, never a route or panel error over a verified answer. Two
// steps: when the stale-chunk listener cancels vite:preloadError, Vite's
// preload helper resolves the import to undefined, skipping the first .then,
// so the undefined guard sits in the second one.
const GenieFigureMark = lazy<ComponentType<FigureMarkProps>>(() =>
  import('./GenieFigureMark')
    .then((module) => module.default)
    .then(
      (mark: ComponentType<FigureMarkProps> | undefined) => ({ default: mark ?? PlainFigure }),
      () => ({ default: PlainFigure }),
    ),
);

/** Verified figures per rendered text: the provider's value. */
export type GenieFigureScopeMap = ReadonlyMap<string, readonly GenieVerifiedClaim[]>;

/** Figures to mark in one MarkdownAnswer, keyed by token. */
export type GenieFigureMarks = ReadonlyMap<string, FigureHighlight>;

export const GenieFigureScopes = createContext<GenieFigureScopeMap | null>(null);

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
  const sections = Array.isArray(payload.sections) ? payload.sections : [];
  // [the text a MarkdownAnswer receives, its items' section label]; the
  // label is the server's `title[:200]` (a code-point prefix).
  const keys: Array<[string, string | null]> = sections.length
    ? [[(payload.summary ?? '').trim(), null], ...sections.map((s): [string, string] => [s.answer, codePointPrefix(s.title, 200)])]
    : [[singleText, null]];
  const scopes = new Map<string, GenieVerifiedClaim[]>();
  for (const [text, label] of keys) {
    const scoped = items.filter((item) => (item.section || null) === label);
    const unique = keys.filter(([other, otherLabel]) => other === text || (label !== null && otherLabel === label)).length === 1;
    if (text && unique && scoped.length > 0) scopes.set(text, scoped);
  }
  return scopes.size > 0 ? scopes : null;
}

/** The marks for one MarkdownAnswer `text`; null without a provider or an entry. */
export function useGenieFigures(text: string, workspaceHost?: string | null): GenieFigureMarks | null {
  const items = useContext(GenieFigureScopes)?.get(text);
  return items ? highlightableFigures(text, items, workspaceHost) : null;
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
    out.push(
      <Suspense key={`figure-${match.index}`} fallback={token}>
        <GenieFigureMark token={token} column={figure.column} />
      </Suspense>,
    );
    last = match.index + token.length;
  }
  if (out.length === 0) return segment;
  if (last < segment.length) out.push(segment.slice(last));
  return out;
}
