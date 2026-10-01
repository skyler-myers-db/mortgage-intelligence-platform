/**
 * Shared, well-formed actor keys for tests (D-identity-review-a1). The shell
 * trusts a health or session key only when it is `actor_` plus 16 lowercase
 * hex characters (lib/healthTrust ACTOR_CACHE_KEY_RE), so short labels such
 * as 'actor_a' now read as untrusted. frontend/tests/e2e/fixture/data/shell.ts
 * spells FIXTURE_ACTOR_A / FIXTURE_ACTOR_B with the same values.
 */
export const ACTOR_A = `actor_${'a'.repeat(16)}`;
export const ACTOR_B = `actor_${'b'.repeat(16)}`;
export const ACTOR_C = `actor_${'c'.repeat(16)}`;
