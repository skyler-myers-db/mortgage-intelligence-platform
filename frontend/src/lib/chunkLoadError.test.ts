import { describe, expect, it } from 'vitest';
import { ChunkLoadError, classifyClientError, isChunkLoadError } from './chunkLoadError';

describe('isChunkLoadError', () => {
  it.each([
    ['Chromium', new TypeError('Failed to fetch dynamically imported module: https://app/assets/lead-queue-0ld.js')],
    ['Firefox', new TypeError('error loading dynamically imported module: https://app/assets/lead-queue-0ld.js')],
    ['Safari', new TypeError('Importing a module script failed.')],
    ['Safari MIME', new TypeError("'application/json' is not a valid JavaScript MIME type.")],
    ['Vite CSS preload', new Error('Unable to preload CSS for /assets/lead-queue-0ld.css')],
    ['prevented preload', new ChunkLoadError()],
  ])('recognises the %s dynamic-import failure shape', (_browser, error) => {
    expect(isChunkLoadError(error)).toBe(true);
    expect(classifyClientError(error)).toBe('chunk');
  });

  it.each([
    ['a render TypeError', new TypeError("Cannot read properties of undefined (reading 'score')")],
    ['an API failure', new Error('Failed to fetch')],
    ['a thrown string', 'Failed to fetch dynamically imported module'],
    ['null', null],
    ['undefined', undefined],
  ])('treats %s as a render error', (_label, error) => {
    expect(isChunkLoadError(error)).toBe(false);
    expect(classifyClientError(error)).toBe('render');
  });
});
