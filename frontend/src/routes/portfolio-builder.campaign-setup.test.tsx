/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true }),
}));

import { DEFAULT_CAMPAIGN_SETUP } from './portfolio-builder.logic';
import { CampaignSetupPanel } from './portfolio-builder.campaign-setup';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;

describe('CampaignSetupPanel', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  it('does not claim analysis is running when the cohort is empty', () => {
    act(() => {
      root.render(
        <MemoryRouter>
          <CampaignSetupPanel
            setup={DEFAULT_CAMPAIGN_SETUP}
            recommendationPending
            recommendationError={false}
            recommendationFetching={false}
            canRecommend={false}
            onFieldChange={() => vi.fn()}
            onToggleHouseholdDedup={vi.fn()}
            onRegenerate={vi.fn()}
            onApply={vi.fn()}
          />
        </MemoryRouter>,
      );
    });

    expect(document.body.textContent).toContain('Run a non-empty portfolio build');
    expect(document.body.textContent).not.toContain('Analyzing the selected cohort');
    const regenerate = [...document.querySelectorAll('button')].find((button) => (
      button.textContent?.includes('Regenerate')
    ));
    expect(regenerate?.disabled).toBe(true);
  });

  it('renders provenance, performance qualification, strategy, and linked evidence', () => {
    const apply = vi.fn();
    act(() => {
      root.render(
        <MemoryRouter>
          <CampaignSetupPanel
            setup={DEFAULT_CAMPAIGN_SETUP}
            recommendation={{
              generation_mode: 'supervisor',
              generator_label: 'Mortgage Growth Supervisor',
              performance_status: 'qualified',
              audience_summary: '2,119 eligible refinance-economics borrowers.',
              strategy: 'Test immediate payment-value clarity against a guidance-led review.',
              variants: [
                {
                  variant_name: 'Benefit-led',
                  subject: 'Review your current mortgage options',
                  body: 'A loan officer can review your current mortgage options with you.',
                  hypothesis: 'Concrete benefit framing improves qualified responses.',
                },
                {
                  variant_name: 'Guidance-led',
                  subject: 'A mortgage review for your next step',
                  body: 'Schedule a no-pressure review with a loan officer.',
                  hypothesis: 'Guidance framing improves trust and response quality.',
                },
              ],
              holdout_pct: 10,
              evidence: [
                {
                  label: 'Eligible cohort',
                  value: '2,119',
                  source_asset: 'mip.gold.borrower_360',
                },
                {
                  label: 'Reached contacts',
                  value: '84 in the last 90 days',
                  source_asset: 'mip_app.call_dispositions',
                },
              ],
              warnings: [],
            }}
            recommendationPending={false}
            recommendationError={false}
            recommendationFetching={false}
            canRecommend
            onFieldChange={() => vi.fn()}
            onToggleHouseholdDedup={vi.fn()}
            onRegenerate={vi.fn()}
            onApply={apply}
          />
        </MemoryRouter>,
      );
    });

    expect(document.body.textContent).toContain('Mortgage Growth Supervisor');
    expect(document.body.textContent).toContain('Qualified team performance');
    expect(document.body.textContent).toContain('2,119 eligible refinance-economics borrowers');
    expect(document.body.textContent).toContain('Test immediate payment-value clarity');
    expect(document.querySelectorAll('.evidence-chip')).toHaveLength(2);
    const applyButton = [...document.querySelectorAll('button')].find((button) => (
      button.textContent?.includes('Apply variants')
    ));
    act(() => applyButton?.click());
    expect(apply).toHaveBeenCalledTimes(1);
  });

  it('requires confirmation before replacing operator-edited campaign copy', () => {
    const apply = vi.fn();
    const editedSetup = {
      ...DEFAULT_CAMPAIGN_SETUP,
      subjectA: 'Operator-edited subject',
      bodyA: 'Operator-edited message',
    };
    act(() => {
      root.render(
        <MemoryRouter>
          <CampaignSetupPanel
            setup={editedSetup}
            recommendation={{
              generation_mode: 'supervisor',
              generator_label: 'Mortgage Growth Supervisor',
              performance_status: 'insufficient_sample',
              audience_summary: 'Selected cohort.',
              strategy: 'Use a reviewed benefit-led test.',
              variants: [
                { variant_name: 'Benefit-led', subject: 'A', body: 'B', hypothesis: 'C' },
                { variant_name: 'Guidance-led', subject: 'D', body: 'E', hypothesis: 'F' },
              ],
              holdout_pct: 10,
              evidence: [],
              warnings: [],
            }}
            recommendationPending={false}
            recommendationError={false}
            recommendationFetching={false}
            canRecommend
            onFieldChange={() => vi.fn()}
            onToggleHouseholdDedup={vi.fn()}
            onRegenerate={vi.fn()}
            onApply={apply}
          />
        </MemoryRouter>,
      );
    });

    const applyButton = [...document.querySelectorAll('button')].find((button) => (
      button.textContent?.includes('Apply variants')
    ));
    act(() => applyButton?.click());
    expect(apply).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('replaces the campaign copy currently in the editor');
    const replace = [...document.querySelectorAll('button')].find((button) => (
      button.textContent?.includes('Replace edited copy')
    ));
    act(() => replace?.click());
    expect(apply).toHaveBeenCalledTimes(1);
  });
});
