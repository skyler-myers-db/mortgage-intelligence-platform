/**
 * Owned storage for fixture specs (D-identity-review-b part F).
 *
 * The app's actor gate (src/lib/actorScope.ts) never adopts private browser
 * data it finds unstamped: a pin, a Genie transcript or a conversation id
 * seeded into bare storage is removed at the first trusted observation (row
 * 1). A spec that seeds private data before boot therefore seeds it OWNED:
 * both owner stamps ('mip.actorOwner' in localStorage, 'mip.actorCacheKey' in
 * this tab's sessionStorage) name the actor the mocked /api/session and
 * /api/health will report ('~nobody' for the default fixtures, whose session
 * and health bodies carry a null / absent actor_cache_key).
 *
 * One addInitScript per call, applied once per tab: a sessionStorage marker
 * remembers the seed, so a reload keeps what the app wrote since (the same
 * pattern as app.ts's preference SEEDS). Key names are literals: this
 * directory may not import runtime src (docs/testing.md, "Owned storage in
 * fixture specs").
 */
import type { Page } from '@playwright/test';

/** The owner of data no actor owns: the default fixtures' actor. */
export const FIXTURE_NOBODY = '~nobody';

const LOCAL_OWNER_KEY = 'mip.actorOwner';
const TAB_OWNER_KEY = 'mip.actorCacheKey';
const SEED_MARKER = 'mip.fixture.ownedSeed';

export interface OwnedStorageSeed {
  local?: Record<string, string>;
  session?: Record<string, string>;
}

export async function seedOwnedStorage(page: Page, owner: string, seed: OwnedStorageSeed = {}): Promise<void> {
  await page.addInitScript(
    ([localOwnerKey, tabOwnerKey, marker, who, local, session]) => {
      try {
        if (window.sessionStorage.getItem(marker) === who) return;
        window.localStorage.setItem(localOwnerKey, who);
        window.sessionStorage.setItem(tabOwnerKey, who);
        for (const [key, value] of Object.entries(local)) window.localStorage.setItem(key, value);
        for (const [key, value] of Object.entries(session)) window.sessionStorage.setItem(key, value);
        window.sessionStorage.setItem(marker, who);
      } catch {
        // Storage is unavailable on about:blank; the next document seeds it.
      }
    },
    [LOCAL_OWNER_KEY, TAB_OWNER_KEY, SEED_MARKER, owner, seed.local ?? {}, seed.session ?? {}] as const,
  );
}
