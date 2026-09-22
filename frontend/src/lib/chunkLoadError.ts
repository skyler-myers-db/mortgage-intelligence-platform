/**
 * Dynamic-import ("chunk load") failure detection.
 *
 * A tab left open across a deploy asks for hashed chunks the new build has
 * retired; the backend answers 404 and the browser rejects the `import()`.
 * Browsers do not share an error class for this, so the only portable signal
 * is the error name / message shape:
 *
 *   Chromium  TypeError  "Failed to fetch dynamically imported module: <url>"
 *   Firefox   TypeError  "error loading dynamically imported module: <url>"
 *   Safari    TypeError  "Importing a module script failed."
 *   Safari    TypeError  "'text/html' is not a valid JavaScript MIME type."
 *   Vite      Error      "Unable to preload CSS for /assets/<file>.css"
 *
 * The distinction matters to the error boundary: React.lazy caches a
 * rejected import for the life of the page, so a chunk failure can only be
 * fixed by a reload, whereas a render error can be retried in place.
 */

export type ClientErrorKind = 'chunk' | 'render';

const CHUNK_ERROR_NAMES = new Set(['ChunkLoadError']);

const CHUNK_MESSAGE_PATTERNS: readonly RegExp[] = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /is not a valid javascript mime type/i,
  /unable to preload css/i,
  /loading (?:css )?chunk [\w-]+ failed/i,
];

/**
 * Thrown by `lazyWithPreload` when a loader settles without a usable module.
 * That happens when a `vite:preloadError` listener calls `preventDefault()`:
 * Vite then swallows the import rejection and resolves `undefined`.
 */
export class ChunkLoadError extends Error {
  constructor(message = 'Lazy module resolved without a default export') {
    super(message);
    this.name = 'ChunkLoadError';
  }
}

export function isChunkLoadError(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false;
  const { name, message } = error as { name?: unknown; message?: unknown };
  if (typeof name === 'string' && CHUNK_ERROR_NAMES.has(name)) return true;
  if (typeof message !== 'string') return false;
  return CHUNK_MESSAGE_PATTERNS.some((pattern) => pattern.test(message));
}

export function classifyClientError(error: unknown): ClientErrorKind {
  return isChunkLoadError(error) ? 'chunk' : 'render';
}
