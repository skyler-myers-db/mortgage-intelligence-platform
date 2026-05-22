import type { ReactNode } from 'react';

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

/** Tiny markdown renderer for Genie answers: bold, inline code, bullets. */
function renderInlineMd(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*([^*]+?)\*\*|`([^`]+?)`)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) {
      out.push(<span key={key++}>{text.slice(last, match.index)}</span>);
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
    out.push(<span key={key++}>{text.slice(last)}</span>);
  }
  return out;
}

export function MarkdownAnswer({ text }: { text: string }) {
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
              {renderInlineMd(b.text)}
            </p>
          ) : (
            <ul
              key={i}
              className="genie-md-list"
            >
              {b.items.map((it, j) => (
                <li key={j}>{renderInlineMd(it)}</li>
              ))}
            </ul>
          ),
        )}
    </>
  );
}

