import { describe, expect, it } from 'vitest';
import { Button, Chip, EvidenceChip } from './Primitives';

// The title passthrough is gone (audit critic-08): `tsc -b` fails if one comes back.
const removed = [
  // @ts-expect-error Chip has no title prop; use tooltip.
  <Chip key="chip" title="x">x</Chip>,
  // @ts-expect-error Button omits the DOM title attribute; use tooltip.
  <Button key="button" title="x">x</Button>,
  // @ts-expect-error EvidenceChip has no title prop; the evidence hover card replaced it.
  <EvidenceChip key="evidence" title="x">x</EvidenceChip>,
];

describe('the removed title passthrough', () => {
  it('is a type error on all three primitives (tsc -b pins it)', () => {
    expect(removed).toHaveLength(3);
  });
});
