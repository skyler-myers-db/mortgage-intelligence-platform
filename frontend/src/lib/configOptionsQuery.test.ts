import { describe, expect, it } from 'vitest';
import {
  CONFIG_OPTIONS_STALE_MS,
  configOptionsQueryOptions,
} from './configOptionsQuery';
import { queryKeys } from './queryKeys';

describe('configOptionsQueryOptions', () => {
  it('keeps config options cache policy in one place', () => {
    const options = configOptionsQueryOptions();

    expect(options.queryKey).toEqual(queryKeys.configOptions());
    expect(options.staleTime).toBe(CONFIG_OPTIONS_STALE_MS);
    expect(options.retry).toBe(false);
  });
});
