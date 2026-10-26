/**
 * Glossary search (W5d, critic-07; deviation:glossary-search). Pure and
 * audit-free: it filters the in-bundle glossary and reads nothing.
 *
 * Text is compared after NFKD normalization with combining marks stripped
 * and lower-cased, so 'refinance' finds 'Refinance' and 'credito' finds
 * 'crédito'. A query is split on whitespace; EVERY token must appear in the
 * term, one of its aliases (shown or search-only) or its short definition.
 */
import type { GlossaryEntry } from './mortgageGlossary';

export function normalizeGlossaryText(text: string): string {
  return text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
}

/** The query's tokens, normalized; none for a blank query. */
export function glossaryQueryTokens(query: string): string[] {
  return normalizeGlossaryText(query).split(/\s+/).filter((token) => token.length > 0);
}

/** Whether an entry matches every token (a blank query matches every entry). */
export function matchesGlossaryQuery(
  entry: Pick<GlossaryEntry, 'term' | 'aliases' | 'searchAliases' | 'short'>,
  tokens: readonly string[],
): boolean {
  if (tokens.length === 0) return true;
  // Fields joined by a newline: a token has no whitespace, so it can only
  // match inside one field.
  const haystack = [entry.term, ...entry.aliases, ...(entry.searchAliases ?? []), entry.short]
    .map(normalizeGlossaryText)
    .join('\n');
  return tokens.every((token) => haystack.includes(token));
}
