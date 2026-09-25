import { describe, expect, it } from 'vitest';
import { formatStepParams } from './ask-genie.compose-plan-params';

const LABELS: Record<string, string> = { itm: 'Prime Refi Candidates', listed: 'Listed for Sale' };
const label = (code: string) => LABELS[code] ?? code;

describe('formatStepParams', () => {
  it('formats the closed vocabulary in display order', () => {
    expect(formatStepParams({ segment_mode: 'all', states: ['IL', 'TX'], segment_codes: ['itm', 'listed'] }, label)).toBe(
      'States: IL, TX · Segments: Prime Refi Candidates, Listed for Sale · Match: all segments',
    );
    expect(formatStepParams({ segment_mode: 'any' })).toBe('Match: any segment');
    expect(formatStepParams({ min_opportunity_score: 80 })).toBe('Min score: 80');
    expect(formatStepParams({ segment_codes: ['itm'] })).toBe('Segments: itm');
  });

  it('reads an empty state list as current coverage and no inputs as none', () => {
    expect(formatStepParams({ states: [] })).toBe('States: current coverage');
    expect(formatStepParams({})).toBe('No inputs (current coverage)');
  });

  it('never renders a key outside the vocabulary', () => {
    expect(formatStepParams({ address_line: '1 Main St', zip5: '60601', states: ['IL'] })).toBe('States: IL');
    expect(formatStepParams({ address_line: '1 Main St' })).toBe('No inputs (current coverage)');
  });
});
