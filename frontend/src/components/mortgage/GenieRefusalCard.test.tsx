/**
 * @vitest-environment happy-dom
 *
 * GenieRefusalCard rendered-DOM tests (audit 2026-09-21 `genie-05`):
 *   - a withheld turn renders the family sentence and its pre-validated chips
 *     inside <GenieAnswer>, and a trusted answer renders no card
 *   - a chip asks its text through onFollowUp; "Edit question" hands the
 *     ORIGINAL question back unchanged
 *   - "This was legitimate" POSTs the hash and family only (never the
 *     question), once, and shows the confirmation, whenever the consented
 *     step is not offered (capture off, no question, pii_request, no provider)
 *   - with capture on it reveals the consented step (D-audit-reads-d): focus
 *     moves in, each button posts its body (question_text only on "Report with
 *     my question"), Esc and Cancel return focus, and a hash-only answer to a
 *     text post reads "Reported without your question"
 *   - without a report hash (older backend) the report control is absent
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
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
    },
  };
});
vi.mock('../../lib/apiClients/genieRefusalReport', () => ({
  genieRefusalReportApi: { report: (...args: unknown[]) => genieRefusalReport(...args) },
}));

import { GenieAnswer } from './GenieAnswer';
import { GenieRefusalCard } from './GenieRefusalCard';
import { REFUSAL_CAPTURE_DISCLOSURE } from './GenieRefusalReportConfirm';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const QUESTION = 'Which zyrplax borrowers are eligible for a HELOC?';
const HASH = 'a'.repeat(64);

function refused(reason: GenieRefusalReason, overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: 'I cannot select or rank borrowers on that criterion.',
    question: QUESTION,
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
        payload={{ answer: '124,946 borrowers.', question: QUESTION, source: 'genie', trusted_assets: ['mip.gold.borrower_360'] }}
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


/** Wait out the lazy confirm chunk (a dynamic import) and its render. */
async function settle() {
  for (let i = 0; i < 4; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

function sessionClient(captureEnabled: boolean): QueryClient {
  const client = new QueryClient();
  client.setQueryData(['session', 'access'], {
    can_access_admin: false,
    can_approve: false,
    refusal_text_capture_enabled: captureEnabled,
  });
  return client;
}

describe('GenieRefusalCard consented capture (D-audit-reads-d)', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    genieRefusalReport.mockReset();
    genieRefusalReport.mockResolvedValue({ accepted: true, duplicate: false, report_id: 'r-1', question_captured: true });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function renderCard(options: { capture?: boolean; reason?: GenieRefusalReason; question?: string | null; provider?: boolean } = {}) {
    const { capture = true, reason = 'unreviewed_criterion', question = QUESTION, provider = true } = options;
    const card = (
      <GenieRefusalCard payload={refused(reason, { conversation_id: 'conv-1' })} question={question ?? undefined} />
    );
    act(() => root.render(provider ? <QueryClientProvider client={sessionClient(capture)}>{card}</QueryClientProvider> : card));
  }
  const reportButton = () => container.querySelector<HTMLButtonElement>('[data-testid="genie-refusal-report"]');
  const confirm = () => container.querySelector<HTMLElement>('[data-testid="genie-refusal-confirm"]');

  it('reveals the step, names it from the report button and moves focus to its first button', async () => {
    renderCard();
    expect(reportButton()?.getAttribute('aria-expanded')).toBe('false');
    act(() => reportButton()?.click());
    await settle();
    const step = confirm();
    expect(step).not.toBeNull();
    expect(reportButton()?.getAttribute('aria-expanded')).toBe('true');
    expect(reportButton()?.getAttribute('aria-controls')).toBe(step?.id);
    expect(container.querySelector('[data-testid="genie-refusal-question"]')?.textContent).toBe(QUESTION);
    expect(step?.querySelector('.genie-answer__refusal-disclosure')?.textContent).toBe(REFUSAL_CAPTURE_DISCLOSURE);
    expect(document.activeElement?.textContent).toBe('Report with my question');
    expect(genieRefusalReport).not.toHaveBeenCalled();
  });

  it('"Report with my question" posts the question exactly as asked, once', async () => {
    renderCard();
    act(() => reportButton()?.click());
    await settle();
    const withButton = container.querySelector<HTMLButtonElement>('[data-testid="genie-refusal-report-with"]');
    act(() => {
      withButton?.click();
      withButton?.click();
    });
    await settle();
    expect(genieRefusalReport).toHaveBeenCalledTimes(1);
    expect(genieRefusalReport.mock.calls[0][0]).toEqual({
      question_hash: HASH,
      refusal_reason: 'unreviewed_criterion',
      conversation_id: 'conv-1',
      message_id: null,
      question_text: QUESTION,
    });
    expect(confirm()).toBeNull();
    const done = container.querySelector<HTMLElement>('.genie-answer__refusal-reported');
    expect(done?.textContent).toContain('Reported for review');
    expect(done?.getAttribute('role')).toBe('status');
    expect(document.activeElement).toBe(done);
  });

  it('"Report without it" posts the hash-only body', async () => {
    renderCard();
    act(() => reportButton()?.click());
    await settle();
    act(() => container.querySelector<HTMLButtonElement>('[data-testid="genie-refusal-report-without"]')?.click());
    await settle();
    const body = genieRefusalReport.mock.calls[0][0] as Record<string, unknown>;
    expect(body).toEqual({ question_hash: HASH, refusal_reason: 'unreviewed_criterion', conversation_id: 'conv-1', message_id: null });
    expect('question_text' in body).toBe(false);
  });

  it('says "Reported without your question" when the server kept the report hash-only', async () => {
    genieRefusalReport.mockResolvedValueOnce({ accepted: true, duplicate: false, question_captured: false });
    renderCard();
    act(() => reportButton()?.click());
    await settle();
    act(() => container.querySelector<HTMLButtonElement>('[data-testid="genie-refusal-report-with"]')?.click());
    await settle();
    expect(container.querySelector('.genie-answer__refusal-reported')?.textContent).toContain('Reported without your question');
  });

  it('Esc and Cancel close the step and return focus to "This was legitimate"', async () => {
    renderCard();
    act(() => reportButton()?.click());
    await settle();
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(confirm()).toBeNull();
    expect(document.activeElement).toBe(reportButton());
    expect(reportButton()?.getAttribute('aria-expanded')).toBe('false');

    act(() => reportButton()?.click());
    await settle();
    act(() => container.querySelector<HTMLButtonElement>('[data-testid="genie-refusal-cancel"]')?.click());
    expect(confirm()).toBeNull();
    expect(document.activeElement).toBe(reportButton());
    expect(genieRefusalReport).not.toHaveBeenCalled();
  });

  it.each([
    ['capture is disabled', { capture: false }],
    ['the refusal was a PII request', { reason: 'pii_request' as const }],
    ['the card holds no question', { question: null }],
    ['there is no QueryClientProvider', { provider: false }],
  ])('files one-click hash-only when %s', async (_label, options) => {
    renderCard(options);
    expect(reportButton()?.hasAttribute('aria-expanded')).toBe(false);
    act(() => reportButton()?.click());
    await settle();
    expect(confirm()).toBeNull();
    expect(genieRefusalReport).toHaveBeenCalledTimes(1);
    expect('question_text' in (genieRefusalReport.mock.calls[0][0] as object)).toBe(false);
    expect(container.querySelector('.genie-answer__refusal-reported')?.textContent).toContain('Reported for review');
  });
});
