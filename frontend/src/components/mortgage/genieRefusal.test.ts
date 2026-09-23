/**
 * Refusal families and chips (audit 2026-09-21 `genie-05`).
 *
 * The chip texts themselves are validated through the real backend guard
 * battery by tests/unit/test_genie_refusal_chips.py (the guards are
 * Python-only). This side pins the contract that test relies on: every wire
 * family has copy and 2-3 chips, the copy never blames the guard, and the
 * family resolver degrades honestly for older backends.
 */
import { describe, expect, it } from 'vitest';
import type { GenieRefusalReason } from '../../types';
import {
  GENIE_REFUSAL_FAMILIES,
  isWithheldGenieSource,
  refusalFamilyFor,
  refusalRephraseChips,
} from './genieRefusal';
import chips from './genieRefusalChips.json';

const FAMILIES: GenieRefusalReason[] = [
  'protected_class',
  'unreviewed_criterion',
  'pii_request',
  'instruction_override',
  'outreach_instruction',
  'scope_bypass',
  'out_of_scope',
  'output_policy',
  'unknown',
];

describe('genieRefusal families', () => {
  it('has copy and 2-3 distinct pre-validated chips for every wire family', () => {
    expect(Object.keys(GENIE_REFUSAL_FAMILIES).sort()).toEqual([...FAMILIES].sort());
    expect(Object.keys(chips).sort()).toEqual([...FAMILIES].sort());
    for (const family of FAMILIES) {
      const list = refusalRephraseChips(family);
      expect(list.length).toBeGreaterThanOrEqual(2);
      expect(list.length).toBeLessThanOrEqual(3);
      expect(new Set(list).size).toBe(list.length);
      expect(list).toEqual(chips[family]);
      expect(GENIE_REFUSAL_FAMILIES[family].title.length).toBeGreaterThan(5);
      expect(GENIE_REFUSAL_FAMILIES[family].sentence.length).toBeGreaterThan(40);
    }
  });

  it('explains what Genie answers and never says the guard was wrong', () => {
    for (const family of FAMILIES) {
      const text = `${GENIE_REFUSAL_FAMILIES[family].title} ${GENIE_REFUSAL_FAMILIES[family].sentence}`;
      expect(text).not.toMatch(/\b(mistake|wrong|false positive|bug|error|sorry)\b/i);
      // No guard oracle: rule names and the ledger's finer codes stay off screen.
      expect(text).not.toMatch(/protected_class|proxy detector|regex|guardrail rule/i);
    }
  });

  it('resolves the family from the wire field and degrades honestly without it', () => {
    expect(refusalFamilyFor({ source: 'refused', refusal_reason: 'pii_request' })).toBe('pii_request');
    expect(refusalFamilyFor({ source: 'refused', refusal_reason: null })).toBe('unknown');
    expect(refusalFamilyFor({ source: 'refused' })).toBe('unknown');
    expect(refusalFamilyFor({ source: 'policy_blocked' })).toBe('output_policy');
    // An unexpected token from a newer backend is not trusted as a family.
    expect(
      refusalFamilyFor({ source: 'refused', refusal_reason: 'protected_class_proxy' as GenieRefusalReason }),
    ).toBe('unknown');
  });

  it('renders the card only for the two withheld sources', () => {
    expect(isWithheldGenieSource('refused')).toBe(true);
    expect(isWithheldGenieSource('policy_blocked')).toBe(true);
    for (const source of ['genie', 'trusted_sql', 'degraded', 'data_gap', 'out_of_footprint', undefined, null]) {
      expect(isWithheldGenieSource(source)).toBe(false);
    }
  });
});
