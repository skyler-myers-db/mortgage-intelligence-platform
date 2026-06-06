import { describe, expect, it } from 'vitest';
import { BORROWER_DOSSIER_LABEL } from './borrower-360';

describe('borrower 360 copy', () => {
  it('uses the selected dossier label', () => {
    expect(BORROWER_DOSSIER_LABEL).toBe('Borrower dossier');
  });
});
