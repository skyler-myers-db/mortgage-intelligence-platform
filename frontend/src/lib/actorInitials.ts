/**
 * Initials for the topbar account avatar (2026-09-21 audit shell-06): the
 * signed-in actor's OWN display name, never a borrower's. A word counts
 * only when its first code point is a letter, so an application id or a
 * numeric principal ('4f3a9c1e-...', '12 34') yields no initials and the
 * trigger shows the account glyph instead. First word plus last word, upper
 * cased in a fixed locale, at most two characters; null when there are none.
 */
const LETTER = /^\p{L}/u;

export function actorInitials(name: string | null | undefined): string | null {
  const words = (name ?? '').trim().split(/\s+/).filter((word) => word.length > 0);
  const picked = words.length > 1 ? [words[0], words[words.length - 1]] : words;
  const initials = picked
    .map((word) => String.fromCodePoint(word.codePointAt(0) ?? 0))
    .filter((first) => LETTER.test(first))
    .join('');
  if (!initials) return null;
  return [...initials.toLocaleUpperCase('en-US')].slice(0, 2).join('');
}
