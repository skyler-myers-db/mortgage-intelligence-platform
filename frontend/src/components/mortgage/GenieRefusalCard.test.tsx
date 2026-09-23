/**
 * @vitest-environment happy-dom
 *
 * GenieRefusalCard rendered-DOM tests (audit 2026-09-21 `genie-05`):
 *   - a withheld turn renders the family sentence and its pre-validated chips
 *     inside <GenieAnswer>, and a trusted answer renders no card
 *   - a chip asks its text through onFollowUp; "Edit question" hands the
 *     ORIGINAL question back unchanged
 *   - "This was legitimate" POSTs the hash and family only (never the
 *     question), once, and shows the confirmation
 *   - without a report hash (older backend) the report control is absent
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer as GenieAnswerShape, GenieRefusalReason } from '../../types';
import { GENIE_REFUSAL_FAMILIES, refusalRephraseChips } from './genieRefusal';

vi.mock('../AppContext', () => ({ useApp: () => ({ setDrawer: vi.fn() }) }));

const genieRefusalReport = vi.fn();
vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    api: {
      genieFeedback: vi.fn().mockResolvedValue({ accepted: true }),
      genieRefusalReport: (...args: unknown[]) => genieRefusalReport(...args),
    },
  };
});

import { GenieAnswer } from './GenieAnswer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const QUESTION = 'Which zyrplax borrowers are eligible for a HELOC?';
const HASH = 'a'.repeat(64);

function refused(reason: GenieRefusalReason, overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: 'I cannot select or rank borrowers on that criterion.',
    source: 'refused',
    trusted_assets: [],
    conversation_id: '',
    question_hash: HASH.slice(0, 16),
    refusal_reason: reason,
    refusal_report_hash: HASH,
    table_rows: [],
    follow_up_questions: [],
    ...overrides,
  };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('GenieRefusalCard inside GenieAnswer', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    genieRefusalReport.mockReset();
    genieRefusalReport.mockResolvedValue({ accepted: true, duplicate: false, report_id: 'r-1' });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const FAMILIES = Object.keys(GENIE_REFUSAL_FAMILIES) as GenieRefusalReason[];

  it.each(FAMILIES)('renders the %s family sentence and its chips', (reason) => {
    act(() => root.render(
      <GenieAnswer payload={refused(reason)} question={QUESTION} onFollowUp={() => {}} />,
    ));
    const card = container.querySelector<HTMLElement>('[data-testid="genie-refusal-card"]');
    expect(card).not.toBeNull();
    expect(card?.getAttribute('data-refusal-reason')).toBe(reason);
    expect(card?.textContent).toContain(GENIE_REFUSAL_FAMILIES[reason].sentence);
    const chips = Array.from(
      container.querySelectorAll<HTMLButtonElement>('[data-testid="genie-refusal-chip"]'),
    ).map((b) => b.querySelector('.filter__value')?.textContent);
    expect(chips).toEqual([...refusalRephraseChips(reason)]);
  });

  it('renders no card on a trusted answer', () => {
    act(() => root.render(
      <GenieAnswer
        payload={{ answer: '124,946 borrowers.', source: 'genie', trusted_assets: ['mip.gold.borrower_360'] }}
        question={QUESTION}
        onFollowUp={() => {}}
      />,
    ));
    expect(container.querySelector('[data-testid="genie-refusal-card"]')).toBeNull();
  });

  it('asks a chip through onFollowUp and restores the original question on Edit', () => {
    const onFollowUp = vi.fn();
    const onEditQuestion = vi.fn();
    act(() => root.render(
      <GenieAnswer
        payload={refused('unreviewed_criterion')}
        question={QUESTION}
        onFollowUp={onFollowUp}
        onEditQuestion={onEditQuestion}
      />,
    ));
    const [chip] = refusalRephraseChips('unreviewed_criterion');
    act(() => {
      container.querySelector<HTMLButtonElement>('[data-testid="genie-refusal-chip"]')?.click();
    });
    expect(onFollowUp).toHaveBeenCalledWith(chip, null);
    act(() => {
      container.querySelector<HTMLButtonElement>('[data-testid="genie-refusal-edit"]')?.click();
    });
    expect(onEditQuestion).toHaveBeenCalledTimes(1);
    expect(onEditQuestion).toHaveBeenCalledWith(QUESTION);
  });

  it('links the reviewed vocabulary in the glossary', () => {
    act(() => root.render(
      <GenieAnswer payload={refused('protected_class')} question={QUESTION} onFollowUp={() => {}} />,
    ));
    const link = container.querySelector<HTMLAnchorElement>('.genie-answer__refusal-actions a');
    expect(link?.getAttribute('href')).toBe('/glossary#reviewed-vocabulary');
  });

  it('reports hash-only, once, and confirms', async () => {
    act(() => root.render(
      <GenieAnswer
        payload={refused('pii_request', { conversation_id: 'conv-1' })}
        question={QUESTION}
        onFollowUp={() => {}}
      />,
    ));
    const button = container.querySelector<HTMLButtonElement>('[data-testid="genie-refusal-report"]');
    expect(button).not.toBeNull();
    act(() => {
      button?.click();
      button?.click();
    });
    await flush();
    expect(genieRefusalReport).toHaveBeenCalledTimes(1);
    const body = genieRefusalReport.mock.calls[0][0] as Record<string, unknown>;
    expect(body).toEqual({
      question_hash: HASH,
      refusal_reason: 'pii_request',
      conversation_id: 'conv-1',
      message_id: null,
    });
    expect(JSON.stringify(body)).not.toContain('zyrplax');
    expect(container.querySelector('[data-testid="genie-refusal-report"]')).toBeNull();
    const confirmation = container.querySelector<HTMLElement>('.genie-answer__refusal-reported');
    expect(confirmation?.textContent).toContain('Reported for review');
    // The button unmounted; focus lands on the confirmation, not <body>.
    expect(document.activeElement).toBe(confirmation);
    expect(confirmation?.getAttribute('tabindex')).toBe('-1');
  });

  it('names the report button with its visible label first (WCAG 2.5.3)', () => {
    act(() => root.render(
      <GenieAnswer payload={refused('protected_class')} question={QUESTION} onFollowUp={() => {}} />,
    ));
    const button = container.querySelector<HTMLButtonElement>('[data-testid="genie-refusal-report"]');
    const visible = button?.textContent?.trim() ?? '';
    const accessibleName = button?.getAttribute('aria-label') ?? visible;
    expect(visible).toBe('This was legitimate');
    expect(accessibleName.startsWith(visible)).toBe(true);
  });

  it('shows only the card chips, not a second row of backend follow-ups, on a refusal', () => {
    act(() => root.render(
      <GenieAnswer
        payload={refused('outreach_instruction', {
          follow_up_questions: ['Which states have the most prime refi candidates?', 'Show the HELOC cohort.'],
        })}
        question={QUESTION}
        onFollowUp={() => {}}
      />,
    ));
    expect(container.querySelectorAll('[data-testid="genie-refusal-chip"]')).toHaveLength(
      refusalRephraseChips('outreach_instruction').length,
    );
    expect(container.querySelector('.genie-answer__followups')).toBeNull();
  });

  it('offers no report control when the turn carries no report hash', () => {
    act(() => root.render(
      <GenieAnswer
        payload={refused('out_of_scope', { refusal_report_hash: null })}
        question={QUESTION}
        onFollowUp={() => {}}
      />,
    ));
    expect(container.querySelector('[data-testid="genie-refusal-card"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="genie-refusal-report"]')).toBeNull();
  });

  it('surfaces a report failure without losing the card', async () => {
    genieRefusalReport.mockRejectedValueOnce(new Error('network'));
    act(() => root.render(
      <GenieAnswer payload={refused('scope_bypass')} question={QUESTION} onFollowUp={() => {}} />,
    ));
    act(() => {
      container.querySelector<HTMLButtonElement>('[data-testid="genie-refusal-report"]')?.click();
    });
    await flush();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('could not be recorded');
    expect(container.querySelector('[data-testid="genie-refusal-report"]')).not.toBeNull();
  });
});
