import { describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  portfolioPreview: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  api: {
    portfolioPreview: apiMocks.portfolioPreview,
  },
  dependencyLabel: vi.fn(),
  isAbortError: vi.fn(),
  isWarmingUpError: vi.fn(),
}));

import {
  APPROVAL_QUEUE_STATE_LABEL,
  HOME_PORTFOLIO_PREVIEW_CRITERIA,
  requestHomePortfolioPreview,
} from './home';

describe('Home portfolio preview request contract', () => {
  it('pins Home KPIs to the global market rather than the eligible-only campaign default', async () => {
    const signal = new AbortController().signal;
    apiMocks.portfolioPreview.mockResolvedValueOnce({});

    await requestHomePortfolioPreview(signal);

    expect(HOME_PORTFOLIO_PREVIEW_CRITERIA).toEqual({ marketing_eligibility: 'Any' });
    expect(apiMocks.portfolioPreview).toHaveBeenCalledWith(
      { marketing_eligibility: 'Any' },
      signal,
    );
  });

  it('describes approval metrics as current lifecycle state, not snapshot state', () => {
    expect(APPROVAL_QUEUE_STATE_LABEL).toBe('current lifecycle state');
  });
});
