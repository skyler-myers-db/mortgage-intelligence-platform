import { useDeferredValue, useRef } from 'react';
import { Link, useLocation } from 'react-router';
import { PageShell } from '../components/layout/PageShell';
import { Chip, SurfaceTitle } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { glossaryQueryTokens, matchesGlossaryQuery } from '../lib/glossarySearch';
import { glossaryEntries, type GlossaryCategory } from '../lib/mortgageGlossary';
import { useGlossaryQuery } from './glossary.queryDraft';
import './glossary.css';

const CATEGORY_LABELS: Record<GlossaryCategory, string> = {
  property: 'Property and ownership',
  mortgage: 'Mortgage math',
  scoring: 'Scoring and offers',
  evidence: 'Evidence and confidence',
  governance: 'Governance',
  principles: 'Product principles & governance',
};

/** The router's `#hash` as an element id ('' when absent or malformed). */
function hashTargetId(hash: string): string {
  try {
    return decodeURIComponent(hash.replace(/^#/, ''));
  } catch {
    return '';
  }
}

export default function GlossaryRoute() {
  // Deep links (`/glossary#clip` from a <GlossaryTerm>, or a category chip
  // below) are scrolled into view by the shell's useMainScroll. `:target`
  // does not follow client-side navigation, so the addressed entry is marked
  // here. Entries and sections take tabIndex=-1 so the shell can also move
  // keyboard / screen-reader focus to the addressed term.
  const location = useLocation();
  const targetId = hashTargetId(location.hash);
  // Search (W5d, critic-07; deviation:glossary-search): the query lives in
  // ?q=, so a filtered glossary can be linked; matching reads nothing. The
  // field shows a synchronous draft that writes ?q= (glossary.queryDraft):
  // controlled from the URL, a router transition would undo keystrokes. The
  // list follows the deferred draft, so typing never waits on the filter.
  const { text: draftQuery, setText: setQuery } = useGlossaryQuery();
  const query = useDeferredValue(draftQuery);
  const tokens = glossaryQueryTokens(query);
  const searchRef = useRef<HTMLInputElement>(null);
  const matching = glossaryEntries.filter((entry) => matchesGlossaryQuery(entry, tokens));
  const grouped = matching.reduce<Record<GlossaryCategory, typeof glossaryEntries>>(
    (acc, entry) => {
      acc[entry.category].push(entry);
      return acc;
    },
    {
      property: [],
      mortgage: [],
      scoring: [],
      evidence: [],
      governance: [],
      principles: [],
    },
  );

  // Categories with no match are hidden in the index and in the sections.
  const categories = (Object.keys(CATEGORY_LABELS) as GlossaryCategory[]).filter(
    (category) => grouped[category].length > 0,
  );
  const searching = tokens.length > 0;

  return (
    <PageShell
      eyebrow="Glossary"
      title="Mortgage intelligence glossary"
      lede="Plain-language definitions for the terms, acronyms, scoring signals, and proof surfaces used throughout Module 0."
      heroRight={
        <Link className="btn btn--primary" to="/lead-queue">
          Back to leads
          <Icon name="chevright" size={14} />
        </Link>
      }
    >
      <div className="glossary-layout">
        <aside className="surface glossary-index" aria-label="Glossary categories">
          <div className="surface__hdr">
            <Icon name="filter" size={14} className="icon-accent" />
            <SurfaceTitle>Categories</SurfaceTitle>
          </div>
          <div className="surface__body">
            {/* A native labelled field (the .field__label / .form-input pair the
                audit explorer's filters use): ui/Field and its stylesheet
                measured +1.5 KiB br on this route's closure. */}
            <label className="glossary-search">
              <span className="field__label">Search the glossary</span>
              <input
                ref={searchRef}
                type="search"
                className="form-input"
                value={draftQuery}
                onChange={(event) => setQuery(event.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            {/* Always mounted (a live region must exist before its text
                changes); visually hidden while there is no query. */}
            <p className={`glossary-search__status muted fs-12${searching ? '' : ' sr-only'}`} role="status" aria-live="polite">
              {searching
                ? matching.length > 0
                  ? `${matching.length} ${matching.length === 1 ? 'term matches' : 'terms match'}`
                  : `No terms match "${query.trim()}"`
                : ''}
            </p>
            {searching && matching.length === 0 && (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  setQuery('');
                  searchRef.current?.focus();
                }}
              >
                Clear search
              </button>
            )}
            <div className="chip-row">
              {categories.map((category) => (
                <Link key={category} className="chip chip--neutral" to={{ search: location.search, hash: `#${category}` }}>
                  <span className="chip__label">{CATEGORY_LABELS[category]}</span>
                </Link>
              ))}
            </div>
          </div>
        </aside>

        <div className="stack-grid">
          {categories.map((category) => (
            <section
              key={category}
              id={category}
              tabIndex={-1}
              className={`surface glossary-section${category === targetId ? ' is-target' : ''}`}
            >
              <div className="surface__hdr">
                <Icon name={category === 'governance' || category === 'principles' ? 'shield' : category === 'evidence' ? 'layers' : 'info'} size={14} className="icon-accent" />
                <div>
                  <SurfaceTitle>{CATEGORY_LABELS[category]}</SurfaceTitle>
                  <div className="muted fs-12">{grouped[category].length} terms</div>
                </div>
              </div>
              <div className="surface__body glossary-grid">
                {grouped[category].map((entry) => (
                  <article
                    key={entry.id}
                    id={entry.id}
                    tabIndex={-1}
                    className={`glossary-entry${entry.id === targetId ? ' is-target' : ''}`}
                  >
                    <div className="split-row">
                      <div>
                        {/* h3: each term sits under its category's h2 (a11y-03). */}
                        <h3 className="glossary-entry__term">{entry.term}</h3>
                        {entry.aliases.length > 0 && (
                          <div className="glossary-entry__aliases">
                            {entry.aliases.map((alias) => (
                              <span key={alias} className="mono">{alias}</span>
                            ))}
                          </div>
                        )}
                      </div>
                      <Chip variant="neutral">{CATEGORY_LABELS[entry.category]}</Chip>
                    </div>
                    <p className="body flush">{entry.short}</p>
                    <div className="glossary-entry__detail">
                      <span className="eyebrow">In this app</span>
                      <p>{entry.appContext}</p>
                    </div>
                    {entry.proof && (
                      <div className="glossary-entry__detail">
                        <span className="eyebrow">How to verify</span>
                        <p>{entry.proof}</p>
                      </div>
                    )}
                  </article>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </PageShell>
  );
}
