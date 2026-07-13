/**
 * @vitest-environment happy-dom
 *
 * GenieAnswer render-surface tests for the frozen payload extensions:
 *   - follow-up chips submit the question through onFollowUp
 *   - native-visualization Beta badge renders only when the field is present,
 *     and is a NEUTRAL marker (never chip--success)
 *   - genuine Genie answers identify the Conversation API and expose
 *     API-provided reasoning summaries in a collapsed disclosure
 *   - feedback control appears on a trusted answer and is suppressed on a
 *     governed refusal
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../../types';

vi.mock('../AppContext', () => ({ useApp: () => ({ setDrawer: vi.fn() }) }));
// The feedback child imports the api client; stub it so mounting never hits
// the network. These render tests don't exercise the POST (that lives in
// GenieAnswerFeedback.test.tsx).
vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, api: { genieFeedback: vi.fn().mockResolvedValue({ accepted: true }) } };
});

import { GenieAnswer } from './GenieAnswer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function payload(overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: 'The average loan age is 5.25 years.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-1',
    message_id: 'msg-1',
    genie_status: 'COMPLETED',
    question_hash: 'h1',
    metric_value: '5.25 years',
    table_rows: null,
    follow_up_questions: [],
    ...overrides,
  } as unknown as GenieAnswerShape;
}

describe('GenieAnswer render surfaces', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('submits the follow-up question text through onFollowUp on click', () => {
    const onFollowUp = vi.fn();
    act(() =>
      root.render(
        <GenieAnswer
          payload={payload({ follow_up_questions: ['Break this down by state'] })}
          question="Q"
          onFollowUp={onFollowUp}
        />,
      ),
    );
    const chip = container.querySelector<HTMLButtonElement>('.filter--question');
    expect(chip).not.toBeNull();
    act(() => chip!.click());
    expect(onFollowUp).toHaveBeenCalledWith('Break this down by state');
  });

  it('renders the native-viz Beta badge only when native_visualization is present', () => {
    // Absent → no badge.
    act(() => root.render(<GenieAnswer payload={payload()} question="Q" onFollowUp={() => {}} />));
    expect(container.querySelector('.genie-answer__native-viz')).toBeNull();

    // Present → neutral marker chip, and NEVER a success chip.
    act(() =>
      root.render(
        <GenieAnswer
          payload={payload({
            native_visualization: { attachment_id: 'att-1', query_attachment_id: null, title: 'Top ZIPs' },
          })}
          question="Q"
          onFollowUp={() => {}}
        />,
      ),
    );
    const badge = container.querySelector('.genie-answer__native-viz');
    expect(badge).not.toBeNull();
    expect(badge!.textContent).toContain('Native chart · Beta');
    const chip = badge!.querySelector('.chip')!;
    expect(chip.classList.contains('chip--neutral')).toBe(true);
    expect(chip.classList.contains('chip--success')).toBe(false);
    // No chart element is rendered from the native descriptor.
    expect(container.querySelector('.genie-answer__native-viz svg.recharts-surface')).toBeNull();
  });

  it('labels genuine Genie answers and renders API reasoning summaries collapsed', () => {
    act(() =>
      root.render(
        <GenieAnswer
          payload={payload({
            reasoning_trace: [
              { kind: 'FILTERING_CONTEXT', content: 'Scoped the request to trusted assets.' },
              { kind: 'THOUGHT_TYPE_TEXT', content: 'Summarized the verified result.' },
            ],
          })}
          question="Q"
          onFollowUp={() => {}}
        />,
      ),
    );

    const source = container.querySelector('.genie-answer__api-source');
    expect(source?.textContent).toContain('Databricks Genie Conversation API');
    expect(source?.getAttribute('aria-label')).toBe(
      'Answer source: Databricks Genie Conversation API',
    );
    const reasoning = container.querySelector<HTMLDetailsElement>('.genie-answer__reasoning');
    expect(reasoning).not.toBeNull();
    expect(reasoning?.open).toBe(false);
    expect(reasoning?.textContent).toContain('API reasoning summary');
    expect(reasoning?.textContent).toContain('Filtering Context');
    expect(reasoning?.textContent).toContain('Scoped the request to trusted assets.');
    expect(reasoning?.textContent).toContain('Text');
    expect(reasoning?.textContent).toContain('Summarized the verified result.');
    expect(reasoning?.textContent).not.toContain('chain-of-thought');
  });

  it('surfaces API reasoning when a real Genie turn is verified by trusted SQL', () => {
    act(() =>
      root.render(
        <GenieAnswer
          payload={payload({
            source: 'trusted_sql',
            reasoning_trace: [
              { kind: 'THOUGHT_TYPE_TEXT', content: 'Verified the returned aggregate.' },
            ],
          })}
          question="Q"
          onFollowUp={() => {}}
        />,
      ),
    );

    expect(container.querySelector('.genie-answer__api-source')?.textContent).toContain(
      'Databricks Genie Conversation API · verified SQL',
    );
    expect(container.querySelector('.genie-answer__reasoning')).not.toBeNull();
    expect(container.textContent).toContain('Verified the returned aggregate.');
  });

  it('shows the feedback control on a trusted answer and hides it on a refusal', () => {
    act(() => root.render(<GenieAnswer payload={payload()} question="Q" onFollowUp={() => {}} />));
    expect(container.querySelector('[data-testid="genie-feedback-up"]')).not.toBeNull();

    act(() =>
      root.render(
        <GenieAnswer
          payload={payload({ source: 'policy_blocked', metric_value: null })}
          question="Q"
          onFollowUp={() => {}}
        />,
      ),
    );
    expect(container.querySelector('[data-testid="genie-feedback-up"]')).toBeNull();
  });
});
