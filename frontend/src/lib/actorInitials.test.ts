/**
 * The account avatar's initials (2026-09-21 audit shell-06; D-theme-nav-e).
 * Only a word that starts with a letter counts, so an application id or a
 * numeric principal falls back to the account glyph.
 */
import { describe, expect, it } from 'vitest';
import { actorInitials } from './actorInitials';

describe('actorInitials', () => {
  it.each([
    ['Jane Doe', 'JD'],
    ['Jane Q Doe', 'JD'],
    ['Skyler Myers', 'SM'],
    ['  Skyler   Myers  ', 'SM'],
    ['jdoe42', 'J'],
    ['émile zola', 'ÉZ'],
    ['jane.doe@summit-mortgage.example', 'J'],
  ])('%j -> %j', (name, expected) => {
    expect(actorInitials(name)).toBe(expected);
  });

  it.each([['4f3a9c1e-7b2d-4c1a'], ['12 34'], [''], ['   '], [null], [undefined]])('%j -> null', (name) => {
    expect(actorInitials(name)).toBeNull();
  });

  it('never returns more than two characters', () => {
    expect(actorInitials('ßtraße Groß')?.length).toBeLessThanOrEqual(2);
  });
});
