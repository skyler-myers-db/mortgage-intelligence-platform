import type { ReactNode } from 'react';
import { SOURCE_LINE_RE, catalogExplorerUrl } from '../../lib/ucAssetLinks';

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
export function renderSourceLinks(text: string, workspaceHost: string | null | undefined): ReactNode[] {
  if (!workspaceHost || !text.includes('.')) return [text];
  const out: ReactNode[] = [];
  const re = new RegExp(SOURCE_LINE_RE.source, SOURCE_LINE_RE.flags);
  let last = 0;
  let key = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const [whole, label, asset] = match;
    const href = catalogExplorerUrl(workspaceHost, asset);
    if (!href) continue;
    if (match.index > last) out.push(text.slice(last, match.index));
    out.push(label);
    out.push(
      <a
        key={`uc-${key++}`}
        className="uc-asset-link"
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        title={`Open ${asset} in Databricks Catalog Explorer`}
      >
        {asset}
      </a>,
    );
    last = match.index + whole.length;
  }
  if (out.length === 0) return [text];
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Tiny markdown renderer for Genie answers: bold, inline code, bullets. */
function renderInlineMd(text: string, workspaceHost?: string | null): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*([^*]+?)\*\*|`([^`]+?)`)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) {
      out.push(<span key={key++}>{renderSourceLinks(text.slice(last, match.index), workspaceHost)}</span>);
    }
    if (match[2] != null) {
      out.push(<strong key={key++}>{match[2]}</strong>);
    } else if (match[3] != null) {
      out.push(
        <code
          key={key++}
          className="inline-code"
        >
          {match[3]}
        </code>,
      );
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    out.push(<span key={key++}>{renderSourceLinks(text.slice(last), workspaceHost)}</span>);
  }
  return out;
}

export function MarkdownAnswer({
  text,
  workspaceHost,
}: {
  text: string;
  /** Workspace origin for Catalog Explorer deep links. Threaded in from
   *  `useWorkspaceHost()` by the caller so this renderer stays pure and
   *  testable. Omitted/null ⇒ "Source: …" stays plain text. */
  workspaceHost?: string | null;
}) {
  const lines = text.split(/\r?\n/);
  type Block = { type: 'p'; text: string } | { type: 'ul'; items: string[] };
  const blocks: Block[] = [];
  for (const raw of lines) {
    const line = raw.trimEnd();
    const bullet = /^\s*([-*•])\s+(.*)$/.exec(line);
    if (bullet) {
      const item = bullet[2];
      const last = blocks[blocks.length - 1];
      if (last && last.type === 'ul') {
        last.items.push(item);
      } else {
        blocks.push({ type: 'ul', items: [item] });
      }
    } else if (line.trim() === '') {
      const last = blocks[blocks.length - 1];
      if (last && last.type === 'p') blocks.push({ type: 'p', text: '' });
    } else {
      const last = blocks[blocks.length - 1];
      if (last && last.type === 'p' && last.text === '') {
        last.text = line;
      } else if (last && last.type === 'p') {
        last.text = `${last.text} ${line.trim()}`;
      } else {
        blocks.push({ type: 'p', text: line });
      }
    }
  }
  return (
    <>
      {blocks
        .filter((b) => (b.type === 'p' ? b.text.length > 0 : b.items.length > 0))
        .map((b, i) =>
          b.type === 'p' ? (
            <p
              key={i}
              className={`genie-md-p ${i === 0 ? 'genie-md-p--first' : ''}`}
            >
              {renderInlineMd(b.text, workspaceHost)}
            </p>
          ) : (
            <ul
              key={i}
              className="genie-md-list"
            >
              {b.items.map((it, j) => (
                <li key={j}>{renderInlineMd(it, workspaceHost)}</li>
              ))}
            </ul>
          ),
        )}
    </>
  );
}
