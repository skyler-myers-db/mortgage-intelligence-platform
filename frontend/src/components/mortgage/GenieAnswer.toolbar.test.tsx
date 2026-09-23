/**
 * @vitest-environment happy-dom
 *
 * Where the answer toolbar (Copy SQL / Copy answer, audit 2026-09-21
 * `genie-06`) appears on the rendered GenieAnswer: under the data of a
 * genuine answer, never on a refusal, a degraded caveat, a data gap or a
 * governed-action receipt (their "answer" is the guardrail's own text).
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../../types';

vi.mock('../AppContext', () => ({ useApp: () => ({ setDrawer: vi.fn() }) }));
vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, api: { genieFeedback: vi.fn().mockResolvedValue({ accepted: true }) } };
});
vi.mock('../HealthProvider', () => ({ useWorkspaceHost: () => null }));

import { GOVERNED_ACTION_SOURCE, GenieAnswer } from './GenieAnswer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function payload(overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: 'Texas leads with 900 in-the-money borrowers.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-1',
    message_id: 'msg-1',
    genie_status: 'COMPLETED',
    table_rows: [
      { state: 'TX', borrowers: 900 },
      { state: 'CA', borrowers: 700 },
    ],
    proof: { sql_query: 'SELECT state, count(*) AS borrowers FROM mip.gold.borrower_360 GROUP BY state', trusted: true },
    ...overrides,
  } as GenieAnswerShape;
}

describe('GenieAnswer toolbar placement', () => {
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

  const mount = (p: GenieAnswerShape) =>
    act(() =>
      root.render(
        <MemoryRouter>
          <GenieAnswer payload={p} />
        </MemoryRouter>,
      ),
    );
  const labels = () =>
    Array.from(container.querySelectorAll('.genie-answer__toolbar button')).map((b) => b.textContent?.trim());

  it('a genuine answer carries Copy SQL and Copy answer, after its table', () => {
    mount(payload());
    expect(labels()).toEqual(['Copy SQL', 'Copy answer']);
    const table = container.querySelector('.genie-answer__table');
    const toolbar = container.querySelector('.genie-answer__toolbar');
    expect(table).not.toBeNull();
    expect(table!.compareDocumentPosition(toolbar!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it.each(['refused', 'degraded', 'data_gap', 'out_of_footprint', 'policy_blocked', GOVERNED_ACTION_SOURCE])(
    'a %s answer carries no toolbar',
    (source) => {
      mount(payload({ source, proof: null, table_rows: [] }));
      expect(container.querySelector('.genie-answer__toolbar')).toBeNull();
    },
  );
});
