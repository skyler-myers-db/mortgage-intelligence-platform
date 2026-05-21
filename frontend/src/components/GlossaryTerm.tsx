import { Link } from 'react-router-dom';
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

export function GlossaryTerm({ term, children, className }: GlossaryTermProps) {
  const entry = glossaryEntry(term);
  const tipId = useId();
  const cls = ['glossary-term', className ?? ''].filter(Boolean).join(' ');
  return (
    <Link
      to={glossaryAnchor(term)}
      className={cls}
      aria-label={`${entry.term}: ${entry.short} Open glossary entry.`}
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
