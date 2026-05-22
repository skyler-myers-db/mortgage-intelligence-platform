import { describe, expect, it } from 'vitest';
import { actionPreview } from './GenieAnswerActions';

describe('actionPreview', () => {
  it('summarizes governed filter criteria without changing action data', () => {
    const preview = actionPreview({
      id: 'act-1',
      label: 'Save leads',
      action_type: 'save_leads',
      description: 'Save a governed lead list.',
      borrower_ids: ['B-1000000000001', 'B-1000000000002'],
      criteria: {
        row_count: 1250,
        result_filters: {
          zips: ['60610', '60611', '60612', '60613', '60614', '60615'],
          states: ['IL'],
          segment_codes: ['ITM', 'HELOC'],
          segment_mode: 'all',
          target_lender_ref: 'Summit Mortgage',
        },
      },
    });

    expect(preview).toEqual([
      '6 ZIPs: 60610, 60611, 60612, 60613, 60614…',
      'States: IL',
      'Segments: ITM, HELOC (all)',
      'Target lien holder: Summit Mortgage',
      '2 borrowers bound by ID',
      '1,250 result rows',
    ]);
  });

  it('ignores malformed filter payloads and still reports row count', () => {
    const preview = actionPreview({
      id: 'act-2',
      label: 'Save leads',
      action_type: 'save_leads',
      description: 'Save a governed lead list.',
      criteria: {
        row_count: 1,
        result_filters: 'not-an-object',
      },
    });

    expect(preview).toEqual(['1 result row']);
  });
});
