/**
 * @vitest-environment happy-dom
 */

import { act, useEffect, useState, type ChangeEvent } from 'react';
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

/** A Field's polite status region, found from its control. */
function notice(input: HTMLElement): HTMLElement {
  const region = input.closest('.field')?.querySelector<HTMLElement>('.field__notice');
  if (!region) throw new Error('no notice region');
  return region;
}

/** The text of each element a control's aria-describedby names, in order. */
function descriptions(input: HTMLElement): string[] {
  return (input.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? `missing #${id}`);
}

/** A FieldReadout: role=group named by its label. */
function readout(label: string): HTMLElement {
  const group = [...document.querySelectorAll<HTMLElement>('[role="group"][aria-labelledby]')]
    .find((node) => document.getElementById(node.getAttribute('aria-labelledby') ?? '')?.textContent === label);
  if (!group) throw new Error(`no readout labelled ${label}`);
  return group;
}

/** Every text-entry control (the elements a 'textbox' role query would return). */
function textboxes(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>('textarea, input:not([type]), input[type="text"], [role="textbox"]')];
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
    // critic-04: the copy reads as text (a labelled readout), never as an
    // input the operator could type into; no textbox exists in the panel.
    expect(textboxes()).toEqual([]);
    expect(readout('Benefit-led subject').querySelector('.field__value')?.textContent).toBe('Operator-edited subject');
    expect(readout('Benefit-led message').querySelector('.field__value')?.textContent).toBe('Operator-edited message');
    expect(readout('Guidance-led subject').querySelector('.field__value')?.textContent)
      .toBe('Not set. Apply a recommendation to fill it.');
    for (const label of ['Benefit-led subject', 'Guidance-led subject', 'Benefit-led message', 'Guidance-led message']) {
      expect(readout(label).querySelector('input, textarea'), label).toBeNull();
    }
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

  /** Campaign setup under a parent that owns the setup state, as the route does. */
  const outside: { current: ((patch: Partial<typeof DEFAULT_CAMPAIGN_SETUP>) => void) | null } = { current: null };
  function StatefulSetup({ commit }: { commit: (field: CampaignNumericField, value: string) => void }) {
    const [setup, setSetup] = useState(DEFAULT_CAMPAIGN_SETUP);
    useEffect(() => {
      outside.current = (patch) => setSetup((current) => ({ ...current, ...patch }));
    }, []);
    return (
      <MemoryRouter>
        <CampaignSetupPanel
          setup={setup}
          recommendationPending={false}
          recommendationError={false}
          recommendationFetching={false}
          canRecommend={false}
          onFieldChange={(key) => (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
            const next = event.target.value;
            setSetup((current) => ({ ...current, [key]: next }));
          }}
          onNumericFieldCommit={(field, value) => {
            commit(field, value);
            setSetup((current) => ({ ...current, [field]: value }));
          }}
          onToggleHouseholdDedup={vi.fn()}
          onRegenerate={vi.fn()}
          onApply={vi.fn()}
        />
      </MemoryRouter>
    );
  }

  function type(input: HTMLInputElement, value: string) {
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it('announces a clamp in the field notice and clears it on the next in-range commit (critic-04)', () => {
    const commit = vi.fn();
    act(() => root.render(<StatefulSetup commit={commit} />));
    const holdout = control('Holdout % (0-50)');
    const region = notice(holdout);
    // The polite region is mounted (and empty) before anything is announced.
    expect(region.getAttribute('role')).toBe('status');
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(region.textContent).toBe('');
    // Only the unit describes the control until a notice is written.
    expect(descriptions(holdout)).toEqual(['percent']);

    type(holdout, '80');
    blur(holdout);
    expect(commit).toHaveBeenLastCalledWith('holdoutPct', '50');
    expect(control('Holdout % (0-50)').value).toBe('50');
    expect(notice(control('Holdout % (0-50)'))).toBe(region);
    expect(region.textContent).toBe('Capped at 50%');
    expect(descriptions(control('Holdout % (0-50)'))).toEqual(['percent', 'Capped at 50%']);

    type(control('Budget'), '20000000');
    blur(control('Budget'));
    expect(commit).toHaveBeenLastCalledWith('budget', '10000000');
    expect(notice(control('Budget')).textContent).toBe('Capped at $10,000,000');

    type(control('Holdout % (0-50)'), '25');
    blur(control('Holdout % (0-50)'));
    expect(commit).toHaveBeenLastCalledWith('holdoutPct', '25');
    expect(region.isConnected).toBe(true);
    expect(region.textContent).toBe('');
    expect(descriptions(control('Holdout % (0-50)'))).toEqual(['percent']);
    // The other field's notice is its own.
    expect(notice(control('Budget')).textContent).toBe('Capped at $10,000,000');
  });

  it('drops a clamp notice once the value changes from outside the field (Apply variants)', () => {
    act(() => root.render(<StatefulSetup commit={vi.fn()} />));
    type(control('Holdout % (0-50)'), '80');
    blur(control('Holdout % (0-50)'));
    const region = notice(control('Holdout % (0-50)'));
    expect(region.textContent).toBe('Capped at 50%');

    // Apply variants writes the recommendation's holdout into the setup: the
    // field now shows 15, and "Capped at 50%" would describe a value it no
    // longer holds.
    act(() => outside.current?.({ holdoutPct: '15' }));
    expect(control('Holdout % (0-50)').value).toBe('15');
    expect(region.isConnected).toBe(true);
    expect(region.textContent).toBe('');
    expect(descriptions(control('Holdout % (0-50)'))).toEqual(['percent']);
  });

  it('announces a rounding to the committed precision, in the field unit (critic-04)', () => {
    const commit = vi.fn();
    act(() => root.render(<StatefulSetup commit={commit} />));
    type(control('Holdout % (0-50)'), '12.346');
    blur(control('Holdout % (0-50)'));
    expect(commit).toHaveBeenLastCalledWith('holdoutPct', '12.35');
    expect(control('Holdout % (0-50)').value).toBe('12.35');
    expect(notice(control('Holdout % (0-50)')).textContent).toBe('Rounded to 12.35%');
    type(control('Email cost'), '0.125');
    blur(control('Email cost'));
    expect(notice(control('Email cost')).textContent).toBe('Rounded to $0.13');
    // A value already at the precision says nothing.
    type(control('Budget'), '1250.50');
    blur(control('Budget'));
    expect(notice(control('Budget')).textContent).toBe('');
  });

  it('shows $ and % beside the numeric controls, hidden from assistive tech, with the unit as the description', () => {
    renderSetup(DEFAULT_CAMPAIGN_SETUP);
    const cases: Array<[string, string, string, 'before' | 'after']> = [
      ['Holdout % (0-50)', '%', 'percent', 'after'],
      ['Budget', '$', 'US dollars', 'before'],
      ['Email cost', '$', 'US dollars', 'before'],
      ['SMS cost', '$', 'US dollars', 'before'],
      ['Mail cost', '$', 'US dollars', 'before'],
    ];
    for (const [label, affix, unit, side] of cases) {
      const input = control(label);
      const wrapper = input.parentElement!;
      expect(wrapper.classList.contains('field__control'), label).toBe(true);
      const affixes = [...wrapper.querySelectorAll<HTMLElement>('.field__affix')];
      expect(affixes.map((node) => [node.textContent, node.getAttribute('aria-hidden')]), label).toEqual([[affix, 'true']]);
      const position = input.compareDocumentPosition(affixes[0]);
      expect(position & (side === 'after' ? Node.DOCUMENT_POSITION_FOLLOWING : Node.DOCUMENT_POSITION_PRECEDING), label)
        .not.toBe(0);
      // The accessible name stays the visible label; the unit is the description.
      expect(input.labels?.[0]?.textContent, label).toBe(label);
      expect(descriptions(input), label).toEqual([unit]);
      expect(document.getElementById(input.getAttribute('aria-describedby')!)?.classList.contains('sr-only'), label).toBe(true);
    }
  });

  it('labels the send window by id, through Field', () => {
    renderSetup({ ...DEFAULT_CAMPAIGN_SETUP, startLocal: '08:30', endLocal: '17:15' });
    for (const [label, value] of [['Send start', '08:30'], ['Send end', '17:15']] as const) {
      const input = control(label);
      expect(input.type, label).toBe('time');
      expect(input.value, label).toBe(value);
      expect(input.hasAttribute('aria-label'), label).toBe(false);
      expect(input.labels?.[0]?.textContent, label).toBe(label);
    }
  });

  it('binds every numeric setup label to its control by id', () => {
    renderSetup(DEFAULT_CAMPAIGN_SETUP);
    for (const label of ['Holdout % (0-50)', 'Budget', 'Email cost', 'SMS cost', 'Mail cost']) {
      const input = control(label);
      expect(input.type, label).toBe('number');
      expect(input.hasAttribute('aria-label'), label).toBe(false);
      expect(input.labels?.[0]?.textContent, label).toBe(label);
    }
  });
});
