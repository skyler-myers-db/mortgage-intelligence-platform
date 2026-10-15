import { useId, type ReactNode } from 'react';
import { parseGenieMarkdownBlocks, tokenizeGenieInline } from '../../lib/genieAnswerText';
import { splitSourceLinks } from '../../lib/genieSourceLinks';
import { SOURCE_LINE_RE } from '../../lib/ucAssetLinks';
import { markFigures, useGenieFigures, type GenieFigureMarks } from './GenieAnswerFigures';
import { Tooltip } from '../ui/Tooltip';

/** Phrases Genie commonly uses to restate the question before answering. */
const RESTATEMENT_LEADERS = [
  /^you want to (see|know|find out|understand)\b/i,
  /^you're (asking|looking for|interested in|curious about)\b/i,
  /^you would like to\b/i,
  /^you'd like to\b/i,
  /^you wanted to\b/i,
  /^to answer your question\b/i,
  /^based on (your question|the data|the available data|what you're asking)\b/i,
  /^let me (answer|address)\b/i,
];

export function stripQuestionRestatement(answer: string): string {
  if (!answer) return answer;
  const trimmed = answer.replace(/^\s+/, '');
  if (!RESTATEMENT_LEADERS.some((re) => re.test(trimmed))) return trimmed;
  const commaEnd = trimmed.search(/,\s/);
  const sentenceEnd = trimmed.search(/[.!?](\s|$)/);
  let cut = -1;
  if (commaEnd !== -1 && sentenceEnd !== -1) {
    cut = Math.min(commaEnd, sentenceEnd);
  } else if (commaEnd !== -1) {
    cut = commaEnd;
  } else if (sentenceEnd !== -1) {
    cut = sentenceEnd;
  }
  if (cut === -1) return trimmed;
  const remainder = trimmed.slice(cut + 1).replace(/^\s+/, '');
  if (remainder.length > 0) {
    return remainder[0].toUpperCase() + remainder.slice(1);
  }
  return trimmed;
}

/**
 * Auto-link the trailing "Source: mip.gold.borrower_360" disclosure Genie
 * appends to its answers, so the reader can jump straight into the workspace
 * Catalog Explorer entry for the asset that produced the number.
 *
 * Degrades to the original plain text whenever the workspace host is unknown
 * (health poll not resolved, anonymous health body, older backend) or the
 * captured token is not a 3-part UC name — a "Source:" line must never
 * render as a dead or fabricated link.
 */
export function renderSourceLinks(
  text: string,
  workspaceHost: string | null | undefined,
  figures: GenieFigureMarks | null = null,
): ReactNode[] {
  // The split is lib/genieSourceLinks', shared with the figure counter
  // (genie-10 phase 2), so a figure is never marked inside the anchor and
  // the counter sees exactly the strings that are marked.
  let key = 0;
  return splitSourceLinks(text, workspaceHost).map((part) =>
    part.kind === 'text' ? (
      markFigures(part.text, figures)
    ) : (
      <Tooltip key={`uc-${key++}`} content={`Open ${part.text} in Databricks Catalog Explorer`}>
        <a className="uc-asset-link" href={part.href} target="_blank" rel="noopener noreferrer">
          {part.text}
        </a>
      </Tooltip>
    ),
  );
}

/**
 * A paragraph that is nothing but one bold run is a section heading. The
 * deep-research sweep titles each planned sub-analysis and its closing
 * synthesis this way (`**<sub-question>**` on its own line), so a ten-section
 * answer needs those lines to read as headings rather than as bold prose.
 */
export function isSectionHeading(text: string): boolean {
  return /^\*\*[^*]+\*\*$/.test(text.trim());
}

/**
 * A paragraph that is NOTHING but "Source: catalog.schema.table" is a
 * footnote, not a claim. Genie closes most answers with one; at body size it
 * competed with the analysis for attention. Same SOURCE_LINE_RE shape as the
 * Catalog Explorer linker (anchored, optional sentence period), so the link
 * behavior is untouched — only the type treatment changes.
 */
const SOURCE_FOOTNOTE_RE = new RegExp(`^${SOURCE_LINE_RE.source}\\.?$`, 'i');

export function isSourceFootnote(text: string): boolean {
  return SOURCE_FOOTNOTE_RE.test(text.trim());
}

/**
 * Inline markup of one block (audit 2026-09-21 `stack-02`/`genie-07`): bold,
 * inline code, guarded italics, and links/images as their plain text. The
 * grammar is lib/genieAnswerText's, shared with the plain-text flatten; the
 * only anchors this renderer ever emits are the reviewed "Source:" links.
 */
function renderInlineMd(text: string, workspaceHost?: string | null, figures: GenieFigureMarks | null = null): ReactNode[] {
  return tokenizeGenieInline(text).map((token, key) => {
    if (token.kind === 'strong') return <strong key={key}>{markFigures(token.text, figures)}</strong>;
    if (token.kind === 'em') return <em key={key}>{markFigures(token.text, figures)}</em>;
    if (token.kind === 'code') {
      return (
        <code key={key} className="inline-code">
          {token.text}
        </code>
      );
    }
    // A link keeps its words only: an LLM-authored URL never becomes an
    // anchor and an image URL is never fetched.
    if (token.kind === 'link') return <span key={key}>{markFigures(token.text, figures)}</span>;
    return <span key={key}>{renderSourceLinks(token.text, workspaceHost, figures)}</span>;
  });
}

/** Verbatim narrative text: a prose pipe table (labelled) or a fenced block. */
function NarrativePre({ lines, narrative }: { lines: string[]; narrative: boolean }) {
  const captionId = useId();
  const text = lines.join('\n');
  return (
    <figure className="genie-md-pre">
      {narrative && (
        <figcaption id={captionId} className="genie-md-pre__label">
          As written in Genie&apos;s narrative
        </figcaption>
      )}
      {/* A named region, focusable so a keyboard can scroll a wide table
          sideways inside it (axe scrollable-region-focusable; a11y-05). Two
          literal branches: oxlint reads only a literal role. */}
      {narrative ? (
        <pre role="region" aria-labelledby={captionId} tabIndex={0}>{text}</pre>
      ) : (
        <pre role="region" aria-label="Verbatim text from the answer" tabIndex={0}>{text}</pre>
      )}
    </figure>
  );
}

export function MarkdownAnswer({
  text,
  workspaceHost,
  headingLevel = 3,
}: {
  text: string;
  /** Workspace origin for Catalog Explorer deep links. Threaded in from
   *  `useWorkspaceHost()` by the caller so this renderer stays pure and
   *  testable. Omitted/null ⇒ "Source: …" stays plain text. */
  workspaceHost?: string | null;
  /** Level of the headings this text carries: 3 in a single-turn answer, 4
   *  inside a deep-research section, whose own title is the h3 (genie-08). */
  headingLevel?: 3 | 4;
}) {
  const Heading = headingLevel === 4 ? 'h4' : 'h3';
  // genie-10 phase 2: the cell figures GenieAnswer located in THIS text, or
  // null (no provider, no entry): then every node below is unchanged.
  const figures = useGenieFigures(text, workspaceHost);
  return (
    <>
      {parseGenieMarkdownBlocks(text).map((b, i) => {
        const first = i === 0 ? ' genie-md-p--first' : '';
        if (b.type === 'h' || (b.type === 'p' && isSectionHeading(b.text))) {
          return (
            <Heading key={i} className={`genie-md-p${first} genie-md-p--heading`}>
              {renderInlineMd(b.text, workspaceHost, figures)}
            </Heading>
          );
        }
        if (b.type === 'p') {
          return (
            <p key={i} className={`genie-md-p${first}${isSourceFootnote(b.text) ? ' genie-md-p--source' : ''}`}>
              {renderInlineMd(b.text, workspaceHost, figures)}
            </p>
          );
        }
        if (b.type === 'pre') return <NarrativePre key={i} lines={b.lines} narrative={b.narrative} />;
        const items = b.items.map((it, j) => <li key={j}>{renderInlineMd(it, workspaceHost, figures)}</li>);
        return b.type === 'ol' ? (
          // `start` keeps the numbering of a list an interleaved bullet split.
          <ol key={i} className="genie-md-list genie-md-list--ordered" start={b.start !== 1 ? b.start : undefined}>
            {items}
          </ol>
        ) : (
          <ul key={i} className="genie-md-list">
            {items}
          </ul>
        );
      })}
    </>
  );
}
