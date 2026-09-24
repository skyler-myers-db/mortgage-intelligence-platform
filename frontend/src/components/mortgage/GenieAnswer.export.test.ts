import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/apiTransport';
import type { GenieAnswerExportReceipt } from '../../lib/apiClients/genieExport';
import {
  buildGenieAnswerCsv,
  exportGenieAnswerCsv,
  GENIE_EXPORT_DOWNLOADED,
  GENIE_EXPORT_NOT_IN_HISTORY,
  GENIE_EXPORT_NOT_RECORDED,
  genieCsvCell,
  type GenieAnswerCsvRequest,
  type GenieExportDeps,
} from './GenieAnswer.export';

/**
 * The audited Genie answer CSV (audit 2026-09-21 `genie-06`, slice 2): the
 * file carries its provenance and raw values through the formula-injection
 * gate, and the download happens ONLY after the GENIE_ANSWER_EXPORT receipt
 * answers. A refusal or an error downloads nothing.
 */

const GENERATED_AT = '2026-09-24T12:00:00.000Z';

function request(overrides: Partial<GenieAnswerCsvRequest> = {}): GenieAnswerCsvRequest {
  return {
    rows: [
      { state: 'IL', zip5: 601, segment_code: 'itm', borrowers: 12045, note: '=HYPERLINK("x")', flag: null },
      { state: 'TX', zip5: '75040', segment_code: 'equity', borrowers: 900.5, note: 'plain, with comma', flag: true },
    ],
    columns: ['state', 'zip5', 'segment_code', 'borrowers', 'note', 'flag'],
    target: {
      conversationId: 'conv-0001',
      messageId: 'msg-0001',
      source: 'trusted_sql',
      trustedAssets: ['mip.semantics.borrower_opportunity_metric_view', 'mip.gold.borrower_360'],
      scope: 'answer',
      sectionIndex: null,
    },
    reportedRowCount: 2,
    ...overrides,
  };
}

const RECEIPT: GenieAnswerExportReceipt = {
  audit_event_id: 'evt-1',
  event_type: 'GENIE_ANSWER_EXPORT',
  actor: 'analyst@summit-mortgage.example',
  scope: 'answer',
  row_count: 2,
  csv_sha256: 'a'.repeat(64),
  columns_sha256: 'b'.repeat(64),
  recorded_at: GENERATED_AT,
};

function deps(post: GenieExportDeps['post']) {
  const order: string[] = [];
  const download = vi.fn((csv: string, filename: string) => {
    order.push(`download:${filename}`);
    return { csv, filename };
  });
  return {
    order,
    download,
    deps: {
      hash: async (text: string) => (text.startsWith('[') ? 'b'.repeat(64) : 'a'.repeat(64)),
      post: vi.fn(async (declaration: Parameters<GenieExportDeps['post']>[0]) => {
        order.push('post');
        return post(declaration);
      }),
      download,
      now: () => new Date(GENERATED_AT),
    } satisfies GenieExportDeps,
  };
}

describe('buildGenieAnswerCsv', () => {
  it('writes provenance, the raw header and raw cells through the formula gate', () => {
    const csv = buildGenieAnswerCsv(request(), GENERATED_AT);
    const lines = csv.split('\n');
    expect(lines.slice(0, 8)).toEqual([
      `# generated_at=${GENERATED_AT}`,
      '# source=trusted_sql',
      '# trusted_assets=mip.semantics.borrower_opportunity_metric_view|mip.gold.borrower_360',
      '# export_scope=answer',
      '# section_index=none',
      '# exported_rows=2',
      '# answer_row_count=2',
      '# rows_complete=true',
    ]);
    expect(lines[8]).toBe('state,zip5,segment_code,borrowers,note,flag');
    // A formula-looking cell is neutralised and quoted; numbers stay ungrouped;
    // a ZIP keeps its leading zero; a segment code reads as its reviewed name;
    // null is empty.
    expect(lines[9]).toBe(`IL,00601,${genieCsvCell('segment_code', 'itm')},12045,"'=HYPERLINK(""x"")",`);
    expect(genieCsvCell('segment_code', 'itm')).not.toBe('itm');
    expect(lines[10]).toBe(`TX,75040,${genieCsvCell('segment_code', 'equity')},900.5,"plain, with comma",true`);
  });

  it('says so when a History replay kept fewer rows than ran', () => {
    const csv = buildGenieAnswerCsv(request({ reportedRowCount: 120 }), GENERATED_AT);
    expect(csv).toContain('# answer_row_count=120');
    expect(csv).toContain('# rows_complete=false');
    const unknown = buildGenieAnswerCsv(request({ reportedRowCount: null }), GENERATED_AT);
    expect(unknown).toContain('# answer_row_count=unknown');
    expect(unknown).toContain('# rows_complete=unknown');
  });

  it('names the section of a deep-research answer', () => {
    const csv = buildGenieAnswerCsv(
      request({ target: { ...request().target, scope: 'section', sectionIndex: 3 } }),
      GENERATED_AT,
    );
    expect(csv).toContain('# export_scope=section');
    expect(csv).toContain('# section_index=3');
  });
});

describe('exportGenieAnswerCsv', () => {
  it('declares ids, counts and digests only, then downloads after the receipt', async () => {
    let releaseReceipt: (receipt: GenieAnswerExportReceipt) => void = () => undefined;
    const held = new Promise<GenieAnswerExportReceipt>((resolve) => {
      releaseReceipt = resolve;
    });
    const harness = deps(() => held);
    const pending = exportGenieAnswerCsv(request(), harness.deps);
    await new Promise((resolve) => setTimeout(resolve, 0));
    // The POST is out; the download waits for its answer.
    expect(harness.order).toEqual(['post']);
    expect(harness.download).not.toHaveBeenCalled();
    expect(harness.deps.post).toHaveBeenCalledWith({
      conversation_id: 'conv-0001',
      message_id: 'msg-0001',
      scope: 'answer',
      row_count: 2,
      answer_row_count: 2,
      csv_sha256: 'a'.repeat(64),
      columns_sha256: 'b'.repeat(64),
    });

    releaseReceipt(RECEIPT);
    const outcome = await pending;

    expect(outcome).toEqual({ kind: 'downloaded', message: GENIE_EXPORT_DOWNLOADED, receipt: RECEIPT });
    expect(harness.order).toEqual(['post', 'download:mip-genie-answer-2026-09-24.csv']);
    const [csv] = harness.download.mock.calls[0];
    expect(csv).toBe(buildGenieAnswerCsv(request(), GENERATED_AT));
  });

  it('downloads nothing when the answer is not in the caller history (404)', async () => {
    const harness = deps(async () => {
      throw new ApiError('Genie answer not found', { path: '/api/genie/export-receipt', status: 404 });
    });
    const outcome = await exportGenieAnswerCsv(request(), harness.deps);
    expect(outcome).toEqual({ kind: 'refused', message: GENIE_EXPORT_NOT_IN_HISTORY });
    expect(harness.download).not.toHaveBeenCalled();
  });

  it.each([422, 429, 503])('downloads nothing when the ledger does not record it (%s)', async (status) => {
    const harness = deps(async () => {
      throw new ApiError('refused', { path: '/api/genie/export-receipt', status });
    });
    const outcome = await exportGenieAnswerCsv(request(), harness.deps);
    expect(outcome).toEqual({ kind: 'refused', message: GENIE_EXPORT_NOT_RECORDED });
    expect(harness.download).not.toHaveBeenCalled();
  });

  it('never declares when the browser cannot hash, and never over the row cap', async () => {
    const harness = deps(async () => RECEIPT);
    const noHash = await exportGenieAnswerCsv(request(), {
      ...harness.deps,
      hash: async () => {
        throw new Error('no WebCrypto');
      },
    });
    expect(noHash.kind).toBe('refused');
    const tooMany = await exportGenieAnswerCsv(
      request({ rows: Array.from({ length: 5001 }, (_, i) => ({ state: 'IL', borrowers: i })) }),
      harness.deps,
    );
    expect(tooMany).toEqual({ kind: 'refused', message: GENIE_EXPORT_NOT_RECORDED });
    expect(harness.deps.post).not.toHaveBeenCalled();
    expect(harness.download).not.toHaveBeenCalled();
  });
});
