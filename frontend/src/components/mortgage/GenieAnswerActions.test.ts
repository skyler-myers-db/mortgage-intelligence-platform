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
          segment_codes: ['itm', 'heloc'],
          segment_mode: 'all',
          target_lender_ref: 'Summit Mortgage',
        },
      },
    });

    expect(preview).toEqual([
      '6 ZIPs: 60610, 60611, 60612, 60613, 60614…',
      'States: IL',
      'Segments: Prime Refi Candidates, HELOC Intent (all selected segments)',
      'Target lien holder: Summit Mortgage',
      '2 borrowers bound by ID',
      '1,250 result rows',
    ]);
  });

  it('uses borrower-facing HELOC Intent labels for segment aliases and default any mode', () => {
    const preview = actionPreview({
      id: 'act-heloc',
      label: 'Open cohort',
      action_type: 'open_lead_queue',
      description: 'Open a governed lead list.',
      criteria: {
        result_filters: {
          // S1.3: permit-activity is no longer an alias for HELOC Intent —
          // it resolves to the real (gated) Permit Activity segment, so both
          // labels appear. HELOC aliases still collapse to HELOC Intent.
          segment_codes: ['HELOC', 'heloc-intent', 'permit-activity'],
        },
      },
    });

    expect(preview).toEqual(['Segments: HELOC Intent, Permit Activity (any selected segment)']);
  });

  it('does not render raw unknown segment codes in governed action chips', () => {
    const preview = actionPreview({
      id: 'act-unknown',
      label: 'Open cohort',
      action_type: 'open_lead_queue',
      description: 'Open a governed lead list.',
      criteria: {
        result_filters: {
          segment_codes: ['retention-risk', 'made_up'],
          segment_mode: 'any',
        },
      },
    });

    expect(preview).toEqual([]);
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
