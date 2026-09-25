import type { Location } from 'react-router';

/**
 * scrollOffsetStore — per-history-entry scroll offsets in sessionStorage,
 * shared by the `.main` scroller (useMainScroll) and the Lead Queue's table
 * scroller (useLeadTableScroll). Moved out of useMainScroll.ts unchanged
 * except that the storage key and the size cap are parameters.
 *
 * Values are plain numbers keyed by the router's random entry key; the
 * first-load entry (key "default") is keyed by a non-reversible fingerprint
 * of its URL, so no URL and no borrower id is ever written to storage.
 */

export type ScrollLocation = Pick<Location, 'key' | 'pathname' | 'search' | 'hash'>;

/** FNV-1a, base36. Only used to avoid writing a URL into sessionStorage. */
export function fingerprint(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36);
}

/**
 * Storage key for a history entry. The router gives every pushed entry a
 * random key; entries it did not create (the first load, a native `#fragment`
 * jump such as the skip link) all report "default", so those are told apart by
 * their URL.
 */
export function scrollStorageKey(location: ScrollLocation): string {
  if (location.key !== 'default') return location.key;
  return `default:${fingerprint(`${location.pathname}${location.search}${location.hash}`)}`;
}

export function readOffsets(storageKey: string, maxEntries: number): Map<string, number> {
  const offsets = new Map<string, number>();
  try {
    const raw = window.sessionStorage.getItem(storageKey);
    if (!raw) return offsets;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return offsets;
    for (const entry of parsed.slice(-maxEntries)) {
      if (!Array.isArray(entry)) continue;
      const [key, value] = entry as unknown[];
      if (typeof key === 'string' && typeof value === 'number' && Number.isFinite(value)) {
        offsets.set(key, value);
      }
    }
  } catch {
    // Storage can be unavailable or hold a corrupt value; start empty.
  }
  return offsets;
}

export function writeOffsets(storageKey: string, offsets: ReadonlyMap<string, number>): void {
  try {
    window.sessionStorage.setItem(storageKey, JSON.stringify([...offsets]));
  } catch {
    // Storage can be unavailable in privacy-restricted contexts.
  }
}

/** Most-recently-used insert with a hard size cap. */
export function rememberOffsetCapped(
  offsets: Map<string, number>,
  key: string,
  value: number,
  maxEntries: number,
): void {
  offsets.delete(key);
  offsets.set(key, value);
  while (offsets.size > maxEntries) {
    const oldest = offsets.keys().next();
    if (oldest.done) break;
    offsets.delete(oldest.value);
  }
}
