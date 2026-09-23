/**
 * SMS segment count for the certified-copy preview (2026-09-21 audit
 * critic-02). Pure: no DOM, no locale.
 *
 * A message that uses only the GSM 03.38 alphabet is sent as GSM-7: 160
 * septets in a single message, 153 per part once it splits (the other seven
 * carry the concatenation header). The extension-table characters
 * (`^ { } \ [ ~ ] | €` and form feed) are sent as an escape plus the character,
 * so each costs two septets. One character outside that alphabet (a curly
 * quote, an em dash, `·`, an emoji) switches the whole message to UCS-2: 70
 * UTF-16 code units single, 67 per part; a character outside the Basic
 * Multilingual Plane (most emoji) is a surrogate pair and costs two.
 *
 * Parts are packed the way carriers split them: an escape pair or a surrogate
 * pair is never cut across two parts, so a boundary can leave a part one unit
 * short and add a segment.
 */

export type SmsEncoding = 'GSM-7' | 'UCS-2';

export interface SmsSegmentCount {
  encoding: SmsEncoding;
  /** Billed units: GSM-7 septets (extension characters count 2) or UTF-16 code units. */
  units: number;
  /** Messages the carrier sends; 0 for empty text. */
  segments: number;
  /** Capacity of each segment at this length: 160 / 70 while single, 153 / 67 per part once split. */
  perSegment: number;
}

const GSM7_BASIC =
  '@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !"#¤%&\'()*+,-./0123456789:;<=>?'
  + '¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà';
const GSM7_EXTENSION = '\f^{}\\[~]|€';

const GSM7_BASIC_SET = new Set(GSM7_BASIC);
const GSM7_EXTENSION_SET = new Set(GSM7_EXTENSION);

const LIMITS: Record<SmsEncoding, { single: number; part: number }> = {
  'GSM-7': { single: 160, part: 153 },
  'UCS-2': { single: 70, part: 67 },
};

/** Septet cost of each character, or null when one falls outside GSM-7. */
function gsm7Costs(characters: readonly string[]): number[] | null {
  const costs: number[] = [];
  for (const character of characters) {
    if (GSM7_BASIC_SET.has(character)) costs.push(1);
    else if (GSM7_EXTENSION_SET.has(character)) costs.push(2);
    else return null;
  }
  return costs;
}

/** Parts needed when no single cost may straddle a part boundary. */
function packedSegments(costs: readonly number[], total: number, single: number, part: number): number {
  if (total === 0) return 0;
  if (total <= single) return 1;
  let segments = 1;
  let used = 0;
  for (const cost of costs) {
    if (used + cost > part) {
      segments += 1;
      used = 0;
    }
    used += cost;
  }
  return segments;
}

export function smsSegments(text: string): SmsSegmentCount {
  // Iterating a string yields code points, so a surrogate pair stays whole.
  const characters = [...text];
  const gsm = gsm7Costs(characters);
  const encoding: SmsEncoding = gsm ? 'GSM-7' : 'UCS-2';
  const costs = gsm ?? characters.map((character) => character.length);
  const units = costs.reduce((sum, cost) => sum + cost, 0);
  const { single, part } = LIMITS[encoding];
  const segments = packedSegments(costs, units, single, part);
  return { encoding, units, segments, perSegment: segments > 1 ? part : single };
}

/** The approver-facing line under the SMS bubble. */
export function describeSmsSegments({ encoding, units, segments, perSegment }: SmsSegmentCount): string {
  const count = `${segments} segment${segments === 1 ? '' : 's'}`;
  const size = segments > 1
    ? `${units} characters at ${perSegment} per segment`
    : `${units} of ${perSegment} characters`;
  const scheme = encoding === 'UCS-2' ? 'UCS-2 (non-GSM characters)' : 'GSM-7';
  return `${count} · ${size} · ${scheme}`;
}
