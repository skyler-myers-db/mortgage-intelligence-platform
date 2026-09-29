/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CampaignRecommendationResponse } from '../types';

const appMocks = vi.hoisted(() => ({ setDrawer: vi.fn() }));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ setDrawer: appMocks.setDrawer, showEvidence: true }),
}));

import {
  CAMPAIGN_NUMERIC_BOUNDS,
  DEFAULT_CAMPAIGN_SETUP,
  type CampaignNumericField,
} from './portfolio-builder.logic';
import { CampaignSetupPanel } from './portfolio-builder.campaign-setup';
import { DRAWER_SOURCES } from '../lib/drawerSources';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;

const CACHED_RECOMMENDATION: CampaignRecommendationResponse = {
  generation_mode: 'supervisor',
  generator_label: 'Mortgage Growth Supervisor',
  performance_status: 'insufficient_sample',
  audience_summary: 'Selected cohort.',
  strategy: 'Use a reviewed benefit-led test.',
  variants: [
    { variant_name: 'Benefit-led', subject: 'A', body: 'B', hypothesis: 'C', provenance_token: null },
    { variant_name: 'Guidance-led', subject: 'D', body: 'E', hypothesis: 'F', provenance_token: null },
  ],
  holdout_pct: 10,
  evidence: [],
  warnings: [],
};

/** The control a Field's <label for> names (critic-04: labels are bound by id, not aria-label). */
function control(label: string): HTMLInputElement {
  const labelEl = [...document.querySelectorAll<HTMLLabelElement>('label.field__label')]
    .find((node) => node.textContent === label);
  const input = labelEl ? document.getElementById(labelEl.htmlFor) : null;
  if (!(input instanceof HTMLInputElement)) throw new Error(`no control labelled ${label}`);
  return input;
}

/** The read-only copy under a FieldReadout label. */
function readout(label: string): HTMLElement {
  const dt = [...document.querySelectorAll<HTMLElement>('dt.field__label')].find((node) => node.textContent === label);
  const dd = dt?.nextElementSibling;
  if (!(dd instanceof HTMLElement) || dd.tagName !== 'DD') throw new Error(`no readout labelled ${label}`);
  return dd;
}

/** A Field's polite status region, found from its control. */
function notice(input: HTMLElement): HTMLElement {
  const region = input.closest('.field')?.querySelector<HTMLElement>('.field__notice');
  if (!region) throw new Error('no notice region');
  return region;
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</output>;
}

describe('CampaignSetupPanel', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    appMocks.setDrawer.mockClear();
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
            onNumericFieldCommit={vi.fn()}
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
        <MemoryRouter initialEntries={['/portfolio-builder']}>
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
                  provenance_token: null,
                },
                {
                  variant_name: 'Guidance-led',
                  subject: 'A mortgage review for your next step',
                  body: 'Schedule a no-pressure review with a loan officer.',
                  hypothesis: 'Guidance framing improves trust and response quality.',
                  provenance_token: null,
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
                {
                  label: 'Applications submitted',
                  value: '12 in the last 90 days',
                  source_asset: 'mip_app.lead_outcomes',
                },
              ],
              warnings: [],
            }}
            recommendationPending={false}
            recommendationError={false}
            recommendationFetching={false}
            canRecommend
            onFieldChange={() => vi.fn()}
            onNumericFieldCommit={vi.fn()}
            onToggleHouseholdDedup={vi.fn()}
            onRegenerate={vi.fn()}
            onApply={apply}
          />
          <LocationProbe />
        </MemoryRouter>,
      );
    });

    expect(document.body.textContent).toContain('Mortgage Growth Supervisor');
    expect(document.body.textContent).toContain('Qualified team performance');
    expect(document.body.textContent).toContain('2,119 eligible refinance-economics borrowers');
    expect(document.body.textContent).toContain('Test immediate payment-value clarity');
    // a11y-05 item 4: each dt/dd pair sits in a plain <div> directly inside
    // the <dl> (valid dl content). A role on the wrapper took the pair out of
    // the list, which axe reported as definition-list + dlitem.
    const list = document.querySelector<HTMLDListElement>('dl.campaign-recommendation__hypothesis-list');
    expect(list).not.toBeNull();
    const hypotheses = [...(list?.children ?? [])] as HTMLElement[];
    expect(hypotheses.map((node) => [
      node.tagName,
      node.getAttribute('role'),
      node.getAttribute('aria-label'),
      [...node.children].map((child) => child.tagName).join('+'),
    ])).toEqual([
      ['DIV', null, null, 'DT+DD'],
      ['DIV', null, null, 'DT+DD'],
    ]);
    expect(hypotheses.map((node) => node.querySelector('dt strong')?.textContent)).toEqual([
      'Benefit-led',
      'Guidance-led',
    ]);
    expect(list?.querySelectorAll('[role]')).toHaveLength(0);
    expect(hypotheses[0].textContent).toContain(
      'Concrete benefit framing improves qualified responses.',
    );
    expect(hypotheses[1].textContent).toContain(
      'Guidance framing improves trust and response quality.',
    );
    expect(document.querySelector('[aria-labelledby="campaign-variant-hypotheses-title"]'))
      .not.toBeNull();
    expect(document.querySelectorAll('.evidence-chip')).toHaveLength(3);
    for (const [assetPath, source] of [
      ['mip_app.call_dispositions', DRAWER_SOURCES.callDispositions],
      ['mip_app.lead_outcomes', DRAWER_SOURCES.leadOutcomes],
    ] as const) {
      const evidenceButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.evidence-chip'))
        .find((button) => button.textContent?.includes(assetPath));
      expect(evidenceButton).toBeTruthy();
      act(() => evidenceButton?.click());
      expect(appMocks.setDrawer).toHaveBeenLastCalledWith(source);
      expect(document.querySelector('[data-testid="location"]')?.textContent)
        .toBe('/portfolio-builder');
    }
    const applyButton = [...document.querySelectorAll('button')].find((button) => (
      button.textContent?.includes('Apply variants')
    ));
    act(() => applyButton?.click());
    expect(apply).toHaveBeenCalledTimes(1);
  });

  it('never exposes admin recovery links to non-admin operators', () => {
    const recommendation: CampaignRecommendationResponse = {
      ...CACHED_RECOMMENDATION,
      evidence: [
        {
          label: 'Unmapped campaign evidence',
          value: 'Unavailable',
          source_asset: 'mip_app.unmapped_campaign_asset',
        },
      ],
    };
    const renderPanel = (canAccessAdmin: boolean) => {
      root.render(
        <MemoryRouter initialEntries={['/portfolio-builder']}>
          <CampaignSetupPanel
            setup={DEFAULT_CAMPAIGN_SETUP}
            recommendation={recommendation}
            recommendationPending={false}
            recommendationError={false}
            recommendationFetching={false}
            canRecommend
            canAccessAdmin={canAccessAdmin}
            onFieldChange={() => vi.fn()}
            onNumericFieldCommit={vi.fn()}
            onToggleHouseholdDedup={vi.fn()}
            onRegenerate={vi.fn()}
            onApply={vi.fn()}
          />
        </MemoryRouter>,
      );
    };

    act(() => renderPanel(false));
    expect(document.body.textContent).toContain('Contact an administrator to review refresh status');
    expect(document.querySelectorAll('a[href^="/admin-config"]')).toHaveLength(0);
    expect(document.querySelector('[aria-label="Unmapped campaign evidence: evidence destination unavailable"]')?.tagName)
      .toBe('SPAN');

    act(() => renderPanel(true));
    expect(Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href^="/admin-config"]'))
      .map((link) => link.getAttribute('href')))
      .toEqual(['/admin-config#data-operations']);
  });

  it('renders borrower copy read-only and applies reviewed variants directly', () => {
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
                { variant_name: 'Benefit-led', subject: 'A', body: 'B', hypothesis: 'C', provenance_token: null },
                { variant_name: 'Guidance-led', subject: 'D', body: 'E', hypothesis: 'F', provenance_token: null },
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
            onNumericFieldCommit={vi.fn()}
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
    // critic-04: the copy is text under its label, with no input or textarea
    // left to type into (or to DOM-tamper); an empty one reads as a muted dash.
    expect(readout('Benefit-led subject').textContent).toBe('Operator-edited subject');
    expect(readout('Benefit-led message').textContent).toBe('Operator-edited message');
    expect(readout('Benefit-led subject').getAttribute('aria-labelledby'))
      .toBe(readout('Benefit-led subject').previousElementSibling?.id);
    const empty = readout('Guidance-led subject');
    expect(empty.querySelector('[aria-hidden="true"]')?.textContent).toBe('—');
    expect(empty.querySelector('.sr-only')?.textContent).toBe('Not set');
    for (const label of ['Benefit-led subject', 'Guidance-led subject', 'Benefit-led message', 'Guidance-led message']) {
      expect(readout(label).closest('.campaign-setup')).not.toBeNull();
      expect(readout(label).closest('dl')?.querySelector('input, textarea')).toBeNull();
    }
    expect(document.querySelectorAll('.campaign-setup textarea')).toHaveLength(0);
    expect(document.querySelectorAll('.campaign-setup input[readonly]')).toHaveLength(0);
    expect(document.body.textContent).toContain('rendered from reviewed server templates');
    act(() => applyButton?.click());
    expect(apply).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).not.toContain('Replace edited copy');
  });

  it('makes a cached recommendation non-actionable when the portfolio becomes empty', () => {
    const apply = vi.fn();
    const editedSetup = {
      ...DEFAULT_CAMPAIGN_SETUP,
      subjectA: 'Operator-edited subject',
    };
    const renderPanel = (canRecommend: boolean) => {
      root.render(
        <MemoryRouter>
          <CampaignSetupPanel
            setup={editedSetup}
            recommendation={CACHED_RECOMMENDATION}
            recommendationPending={false}
            recommendationError={false}
            recommendationFetching={false}
            canRecommend={canRecommend}
            onFieldChange={() => vi.fn()}
            onNumericFieldCommit={vi.fn()}
            onToggleHouseholdDedup={vi.fn()}
            onRegenerate={vi.fn()}
            onApply={apply}
          />
        </MemoryRouter>,
      );
    };

    act(() => renderPanel(true));
    const applyButton = [...document.querySelectorAll<HTMLButtonElement>('button')].find((button) => (
      button.textContent?.includes('Apply variants')
    ));
    act(() => applyButton?.click());
    expect(apply).toHaveBeenCalledTimes(1);

    act(() => renderPanel(false));
    const staleApplyButton = [...document.querySelectorAll<HTMLButtonElement>('button')].find((button) => (
      button.textContent?.includes('Apply variants')
    ));
    expect(staleApplyButton?.disabled).toBe(true);
    expect(document.body.textContent).toContain('Run a non-empty portfolio build');
    expect(document.body.textContent).not.toContain('Replace edited copy');
    act(() => staleApplyButton?.click());
    expect(apply).toHaveBeenCalledTimes(1);
  });

  it('exposes and commits every numeric field boundary on blur', () => {
    const commit = vi.fn();
    const cases: Array<{ field: CampaignNumericField; label: string }> = [
      { field: 'holdoutPct', label: 'Holdout % (0-50)' },
      { field: 'budget', label: 'Budget' },
      { field: 'emailCost', label: 'Email cost' },
      { field: 'smsCost', label: 'SMS cost' },
      { field: 'mailCost', label: 'Mail cost' },
    ];

    for (const { field, label } of cases) {
      const bounds = CAMPAIGN_NUMERIC_BOUNDS[field];
      for (const [raw, expected] of [
        [String(bounds.min - 1), String(bounds.min)],
        [String(bounds.max + 1), String(bounds.max)],
      ]) {
        act(() => {
          root.render(
            <MemoryRouter>
              <CampaignSetupPanel
                setup={{ ...DEFAULT_CAMPAIGN_SETUP, [field]: raw }}
                recommendationPending={false}
                recommendationError={false}
                recommendationFetching={false}
                canRecommend={false}
                onFieldChange={() => vi.fn()}
                onNumericFieldCommit={commit}
                onToggleHouseholdDedup={vi.fn()}
                onRegenerate={vi.fn()}
                onApply={vi.fn()}
              />
            </MemoryRouter>,
          );
        });
        const input = control(label);
        expect(input.min, field).toBe(String(bounds.min));
        expect(input.max, field).toBe(String(bounds.max));
        expect(input.step, field).toBe(String(bounds.step));
        act(() => input.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
        expect(commit).toHaveBeenLastCalledWith(field, expected);
      }
    }
  });

  function renderSetup(setup: typeof DEFAULT_CAMPAIGN_SETUP, commit = vi.fn()) {
    act(() => {
      root.render(
        <MemoryRouter>
          <CampaignSetupPanel
            setup={setup}
            recommendationPending={false}
            recommendationError={false}
            recommendationFetching={false}
            canRecommend={false}
            onFieldChange={() => vi.fn()}
            onNumericFieldCommit={commit}
            onToggleHouseholdDedup={vi.fn()}
            onRegenerate={vi.fn()}
            onApply={vi.fn()}
          />
        </MemoryRouter>,
      );
    });
  }

  function blur(input: HTMLInputElement) {
    act(() => input.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
  }

  it('announces a clamp in the field notice and clears it on the next in-range commit (critic-04)', () => {
    const commit = vi.fn();
    renderSetup({ ...DEFAULT_CAMPAIGN_SETUP, holdoutPct: '80', budget: '20000000' }, commit);
    const holdout = control('Holdout % (0-50)');
    const region = notice(holdout);
    // The polite region is mounted (and empty) before anything is announced.
    expect(region.getAttribute('role')).toBe('status');
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(region.textContent).toBe('');
    expect(holdout.getAttribute('aria-describedby')).toBeNull();

    blur(holdout);
    expect(commit).toHaveBeenLastCalledWith('holdoutPct', '50');
    expect(notice(control('Holdout % (0-50)'))).toBe(region);
    expect(region.textContent).toBe('Capped at 50%');
    expect(control('Holdout % (0-50)').getAttribute('aria-describedby')).toBe(region.id);

    blur(control('Budget'));
    expect(commit).toHaveBeenLastCalledWith('budget', '10000000');
    expect(notice(control('Budget')).textContent).toBe('Capped at $10,000,000');

    renderSetup({ ...DEFAULT_CAMPAIGN_SETUP, holdoutPct: '25', budget: '20000000' }, commit);
    blur(control('Holdout % (0-50)'));
    expect(commit).toHaveBeenLastCalledWith('holdoutPct', '25');
    expect(region.isConnected).toBe(true);
    expect(region.textContent).toBe('');
    expect(control('Holdout % (0-50)').getAttribute('aria-describedby')).toBeNull();
    // The other field's notice is its own.
    expect(notice(control('Budget')).textContent).toBe('Capped at $10,000,000');
  });

  it('binds every setup label to its control and shows the unit adornments', () => {
    renderSetup(DEFAULT_CAMPAIGN_SETUP);
    const adornments = (label: string) => {
      const field = control(label).closest('.field');
      return [...(field?.querySelectorAll<HTMLElement>('.field__adornment') ?? [])].map((node) => [
        node.textContent,
        node.compareDocumentPosition(control(label)) & Node.DOCUMENT_POSITION_FOLLOWING ? 'prefix' : 'suffix',
        node.getAttribute('aria-hidden'),
      ]);
    };
    expect(adornments('Holdout % (0-50)')).toEqual([['%', 'suffix', 'true']]);
    for (const label of ['Budget', 'Email cost', 'SMS cost', 'Mail cost']) {
      expect(adornments(label), label).toEqual([['$', 'prefix', 'true']]);
    }
    for (const label of ['Send start', 'Send end']) {
      expect(adornments(label), label).toEqual([]);
      expect(control(label).type).toBe('time');
    }
    for (const label of ['Holdout % (0-50)', 'Budget', 'Email cost', 'SMS cost', 'Mail cost', 'Send start', 'Send end']) {
      const input = control(label);
      expect(input.hasAttribute('aria-label'), label).toBe(false);
      expect(input.labels?.[0]?.textContent, label).toBe(label);
    }
  });
});
