import { Link } from 'react-router';
import { useId, type ReactNode } from 'react';
import {
  glossaryAnchor,
  glossaryEntry,
  type GlossaryTermKey,
} from '../lib/mortgageGlossary';

interface GlossaryTermProps {
  term: GlossaryTermKey;
  children?: ReactNode;
  className?: string;
}

/**
 * The link's accessible name starts with what it SHOWS (WCAG 2.5.3 label in
 * name): a speech user says "click Not in the money", not the entry's term.
 * When the visible text is the term itself, the name stays term-first.
 */
function accessibleName(children: ReactNode, term: string, short: string): string {
  const visible = typeof children === 'string' ? children.trim() : '';
  const lead = visible && visible !== term ? `${visible}. ` : '';
  return `${lead}${term}: ${short} Open glossary entry.`;
}

export function GlossaryTerm({ term, children, className }: GlossaryTermProps) {
  const entry = glossaryEntry(term);
  const tipId = useId();
  const cls = ['glossary-term', className ?? ''].filter(Boolean).join(' ');
  return (
    <Link
      to={glossaryAnchor(term)}
      className={cls}
      aria-label={accessibleName(children, entry.term, entry.short)}
      aria-describedby={tipId}
    >
      <span>{children ?? entry.term}</span>
      <span id={tipId} className="glossary-term__tip" role="tooltip">
        <span className="glossary-term__tip-title">{entry.term}</span>
        <span>{entry.short}</span>
        <span className="glossary-term__tip-context">{entry.appContext}</span>
      </span>
    </Link>
  );
}
