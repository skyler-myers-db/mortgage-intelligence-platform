import { describe, expect, it } from 'vitest';
import { borrower360Path, genieCellHref, isMaskedBorrowerId, stateLeadQueuePath } from './genieCellLinks';

const BORROWER = 'B-7K2M9QX4TB3PZ';

describe('isMaskedBorrowerId', () => {
  it('accepts the canonical masked id shape', () => {
    expect(isMaskedBorrowerId(BORROWER)).toBe(true);
  });

  it.each([
    ['lowercase', 'b-7k2m9qx4tb3pz'],
    ['too short', 'B-7K2M9QX4TB3'],
    ['too long', 'B-7K2M9QX4TB3PZQ'],
    ['no prefix', '7K2M9QX4TB3PZ'],
    ['number', 12345],
    ['null', null],
  ])('rejects %s', (_label, value) => {
    expect(isMaskedBorrowerId(value)).toBe(false);
  });
});

describe('genieCellHref', () => {
  it('links a borrower_id cell to the borrower 360 route', () => {
    expect(genieCellHref('borrower_id', BORROWER)).toBe(`/borrower-360/${BORROWER}`);
  });

  it('links a state cell to the same state-filtered lead queue the map uses', () => {
    expect(genieCellHref('state', 'IL')).toBe('/lead-queue?state=IL');
    expect(genieCellHref('state', 'il')).toBe('/lead-queue?state=IL');
    expect(genieCellHref('state_code', 'TX')).toBe('/lead-queue?state=TX');
  });

  it('leaves a city cell plain — no route supports a city filter', () => {
    expect(genieCellHref('city', 'Chicago')).toBeNull();
  });

  it('leaves a borrower_id that does not match the masked shape plain', () => {
    expect(genieCellHref('borrower_id', 'unknown')).toBeNull();
    expect(genieCellHref('borrower_id', null)).toBeNull();
  });

  it('leaves a state-looking value in an unrelated column plain', () => {
    expect(genieCellHref('segment_code', 'IL')).toBeNull();
  });

  it('leaves non-2-letter state values plain', () => {
    expect(genieCellHref('state', 'Illinois')).toBeNull();
  });

  it('is case-insensitive on the column name', () => {
    expect(genieCellHref('State', 'IL')).toBe('/lead-queue?state=IL');
    expect(genieCellHref('BORROWER_ID', BORROWER)).toBe(`/borrower-360/${BORROWER}`);
  });
});

describe('path builders', () => {
  it('encodes the borrower id', () => {
    expect(borrower360Path('B-ABC/DEF')).toBe('/borrower-360/B-ABC%2FDEF');
  });

  it('upper-cases the state before building the queue path', () => {
    expect(stateLeadQueuePath(' ca ')).toBe('/lead-queue?state=CA');
  });
});
