/**
 * @vitest-environment happy-dom
 *
 * "Download CSV" under a Genie answer's rows (audit 2026-09-21 `genie-06`,
 * slice 2), at the rendered layer: offered only on a trusted answer with a
 * live conversation and message id; the download starts only after the
 * GENIE_ANSWER_EXPORT receipt resolves; a 404 or 503 downloads nothing and
 * says why through the surface announcer; a latch holds a second POST while
 * the first is recording; above 5,000 rows it is disabled with a reason.
 *
 * A click crosses the lazy export chunk, two digests and the POST, so every
 * test waits for the observable outcome instead of a fixed number of ticks
 * (one `setTimeout(0)` flush was flaky on a loaded machine), and the digest
 * is stubbed so the chain holds no WebCrypto round trip.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../../types';
import type { GenieAnswerExportReceipt } from '../../lib/apiClients/genieExport';

const mocks = vi.hoisted(() => ({
  postGenieExportReceipt: vi.fn(),
  downloadCsvText: vi.fn(),
  genieFeedback: vi.fn(),
}));

vi.mock('../AppContext', () => ({ useApp: () => ({ setDrawer: vi.fn() }) }));
vi.mock('../HealthProvider', () => ({ useWorkspaceHost: () => null }));
vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, api: { genieFeedback: mocks.genieFeedback } };
});
vi.mock('../../lib/apiClients/genieExport', async () => {
  const actual = await vi.importActual<typeof import('../../lib/apiClients/genieExport')>(
    '../../lib/apiClients/genieExport',
  );
  return { ...actual, postGenieExportReceipt: mocks.postGenieExportReceipt };
});
vi.mock('../../lib/csv', async () => {
  const actual = await vi.importActual<typeof import('../../lib/csv')>('../../lib/csv');
  return { ...actual, downloadCsvText: mocks.downloadCsvText };
});
vi.mock('../../lib/apiClients/leadExport', async () => {
  const actual = await vi.importActual<typeof import('../../lib/apiClients/leadExport')>(
    '../../lib/apiClients/leadExport',
  );
  return { ...actual, sha256Hex: async () => 'c'.repeat(64) };
});

import { ApiError } from '../../lib/apiTransport';
import { GenieAnswer } from './GenieAnswer';
import {
  GENIE_EXPORT_DOWNLOADED,
  GENIE_EXPORT_NOT_IN_HISTORY,
  GENIE_EXPORT_NOT_RECORDED,
} from './GenieAnswer.export';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const RECEIPT: GenieAnswerExportReceipt = {
  audit_event_id: 'evt-1',
  event_type: 'GENIE_ANSWER_EXPORT',
  actor: 'analyst@summit-mortgage.example',
  scope: 'answer',
  row_count: 3,
  csv_sha256: 'a'.repeat(64),
  columns_sha256: 'b'.repeat(64),
  recorded_at: '2026-09-24T12:00:00Z',
};

function payload(overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: 'Illinois leads.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-0001',
    message_id: 'msg-0001',
    genie_status: 'COMPLETED',
    row_count: 3,
    table_rows: [
      { state: 'IL', borrowers: 1204 },
      { state: 'TX', borrowers: 900 },
      { state: 'OH', borrowers: 480 },
    ],
    follow_up_questions: [],
    ...overrides,
  } as GenieAnswerShape;
}

/** One act-wrapped tick, so state set by a settling promise renders. */
async function tick(ms = 10) {
  await act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
}

/**
 * Retry `check` on act-wrapped ticks until it stops throwing: the positive
 * outcome a test waits for before it makes any negative assertion.
 */
async function eventually(check: () => void, timeoutMs = 10_000) {
  const startedAt = Date.now();
  for (;;) {
    try {
      check();
      return;
    } catch (error) {
      if (Date.now() - startedAt > timeoutMs) throw error;
    }
    await tick();
  }
}

describe('Genie answer CSV download (genie-06 slice 2)', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onAnnounce = vi.fn();

  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    onAnnounce.mockReset();
    mocks.genieFeedback.mockResolvedValue({ accepted: true });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(value: GenieAnswerShape) {
    act(() =>
      root.render(
        <MemoryRouter>
          <GenieAnswer payload={value} question="Which states lead?" onAnnounce={onAnnounce} />
        </MemoryRouter>,
      ),
    );
  }

  const downloadButton = () =>
    Array.from(container.querySelectorAll<HTMLButtonElement>('button.genie-answer__download'))[0] ?? null;

  it('downloads only after the receipt resolves, and says so', async () => {
    let resolveReceipt: (receipt: GenieAnswerExportReceipt) => void = () => undefined;
    mocks.postGenieExportReceipt.mockReturnValue(
      new Promise<GenieAnswerExportReceipt>((resolve) => {
        resolveReceipt = resolve;
      }),
    );
    render(payload());
    expect(downloadButton()?.textContent).toBe('Download CSV');

    await act(async () => downloadButton()!.click());
    await eventually(() => expect(mocks.postGenieExportReceipt).toHaveBeenCalledTimes(1));
    expect(mocks.downloadCsvText).not.toHaveBeenCalled();
    expect(downloadButton()!.disabled).toBe(true);
    expect(downloadButton()!.textContent).toBe('Recording export…');
    // The declaration carries no question text.
    expect(JSON.stringify(mocks.postGenieExportReceipt.mock.calls[0][0])).not.toContain('Which states');

    await act(async () => resolveReceipt(RECEIPT));
    await eventually(() =>
      expect(container.querySelector('[data-export-status]')?.textContent).toBe(GENIE_EXPORT_DOWNLOADED),
    );
    expect(mocks.downloadCsvText).toHaveBeenCalledTimes(1);
    const [csv] = mocks.downloadCsvText.mock.calls[0] as [string, string];
    expect(csv.split('\n')).toContain('state,borrowers');
    expect(onAnnounce).toHaveBeenCalledWith(GENIE_EXPORT_DOWNLOADED);
    expect(container.querySelector('[data-export-status]')?.textContent).toBe(GENIE_EXPORT_DOWNLOADED);
    expect(downloadButton()!.disabled).toBe(false);
  });

  it('the latch holds a second click while the first is recording: one POST', async () => {
    mocks.postGenieExportReceipt.mockReturnValue(new Promise(() => undefined));
    render(payload());
    const button = downloadButton()!;
    await act(async () => {
      button.click();
      button.click();
    });
    await eventually(() => expect(mocks.postGenieExportReceipt).toHaveBeenCalled());
    // Both clicks started in the same tick; give a second chain time to land.
    for (let i = 0; i < 5; i += 1) await tick();
    expect(mocks.postGenieExportReceipt).toHaveBeenCalledTimes(1);
  });

  it.each([
    [404, GENIE_EXPORT_NOT_IN_HISTORY],
    [503, GENIE_EXPORT_NOT_RECORDED],
    [422, GENIE_EXPORT_NOT_RECORDED],
  ])('a %s downloads nothing and says why', async (status, message) => {
    mocks.postGenieExportReceipt.mockRejectedValue(
      new ApiError('refused', { path: '/api/genie/export-receipt', status }),
    );
    render(payload());
    await act(async () => downloadButton()!.click());
    await eventually(() => {
      expect(onAnnounce).toHaveBeenCalledWith(message);
      expect(container.querySelector('[data-export-status]')?.textContent).toBe(message);
    });
    expect(mocks.postGenieExportReceipt).toHaveBeenCalledTimes(1);
    expect(mocks.downloadCsvText).not.toHaveBeenCalled();
    expect(downloadButton()!.disabled).toBe(false);
  });

  it('is not offered without a live message id, on a governed action result, or on a withheld answer', () => {
    render(payload({ message_id: null, proof: null }));
    expect(downloadButton()).toBeNull();
    render(payload({ source: 'governed_action' }));
    expect(downloadButton()).toBeNull();
    render(payload({ source: 'data_gap' }));
    expect(downloadButton()).toBeNull();
    render(payload({ source: 'trusted_sql' }));
    expect(downloadButton()).not.toBeNull();
  });

  it('is disabled with a reason above 5,000 rows', () => {
    const rows = Array.from({ length: 5001 }, (_, i) => ({ state: 'IL', borrowers: i }));
    render(payload({ table_rows: rows, row_count: 5001 }));
    const button = downloadButton()!;
    expect(button.disabled).toBe(true);
    const reason = document.getElementById(button.getAttribute('aria-describedby') ?? '');
    expect(reason?.textContent).toBe('Download holds at most 5,000 rows; this answer has 5,001.');
  });

  it('offers one download per section of a deep-research answer, scoped to its section', async () => {
    mocks.postGenieExportReceipt.mockResolvedValue({ ...RECEIPT, scope: 'section' });
    render(
      payload({
        summary: 'Summary.',
        sections: [
          { title: 'A', question: 'a?', answer: 'A.', row_count: 2, table_rows: [{ state: 'IL', n: 1 }, { state: 'TX', n: 2 }] },
          { title: 'B', question: 'b?', answer: 'B.', row_count: 1, table_rows: [{ state: 'OH', n: 3 }] },
        ],
      }),
    );
    const buttons = container.querySelectorAll<HTMLButtonElement>('button.genie-answer__download');
    expect(buttons).toHaveLength(2);
    await act(async () => buttons[1].click());
    await eventually(() => expect(onAnnounce).toHaveBeenCalledWith(GENIE_EXPORT_DOWNLOADED));
    expect(mocks.postGenieExportReceipt).toHaveBeenCalledTimes(1);
    expect(mocks.downloadCsvText).toHaveBeenCalledTimes(1);
    expect(mocks.postGenieExportReceipt.mock.calls[0][0]).toMatchObject({
      scope: 'section',
      row_count: 1,
      answer_row_count: 1,
    });
    const [csv] = mocks.downloadCsvText.mock.calls[0] as [string, string];
    expect(csv).toContain('# section_index=2');
  });
});
