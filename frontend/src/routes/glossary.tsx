import { Link } from 'react-router';
import { PageShell } from '../components/layout/PageShell';
import { Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { glossaryEntries, type GlossaryCategory } from '../lib/mortgageGlossary';

const CATEGORY_LABELS: Record<GlossaryCategory, string> = {
  property: 'Property and ownership',
  mortgage: 'Mortgage math',
  scoring: 'Scoring and offers',
  evidence: 'Evidence and confidence',
  governance: 'Governance',
  principles: 'Product principles & governance',
};

export default function GlossaryRoute() {
  const grouped = glossaryEntries.reduce<Record<GlossaryCategory, typeof glossaryEntries>>(
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
            <div className="h-4">Categories</div>
          </div>
          <div className="surface__body">
            <div className="chip-row">
              {Object.entries(CATEGORY_LABELS).map(([category, label]) => (
                <a key={category} className="chip chip--neutral" href={`#${category}`}>
                  <span className="chip__label">{label}</span>
                </a>
              ))}
            </div>
          </div>
        </aside>

        <div className="stack-grid">
          {(Object.keys(CATEGORY_LABELS) as GlossaryCategory[]).map((category) => (
            <section key={category} id={category} className="surface glossary-section">
              <div className="surface__hdr">
                <Icon name={category === 'governance' || category === 'principles' ? 'shield' : category === 'evidence' ? 'layers' : 'info'} size={14} className="icon-accent" />
                <div>
                  <div className="h-4">{CATEGORY_LABELS[category]}</div>
                  <div className="muted fs-12">{grouped[category].length} terms</div>
                </div>
              </div>
              <div className="surface__body glossary-grid">
                {grouped[category].map((entry) => (
                  <article key={entry.id} id={entry.id} className="glossary-entry">
                    <div className="split-row">
                      <div>
                        <h2 className="glossary-entry__term">{entry.term}</h2>
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
