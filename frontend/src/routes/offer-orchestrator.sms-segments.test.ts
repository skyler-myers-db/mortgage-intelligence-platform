import { describe, expect, it } from 'vitest';
import { describeSmsSegments, smsSegments } from './offer-orchestrator.sms-segments';

const a = (count: number) => 'a'.repeat(count);

describe('smsSegments (critic-02)', () => {
  it('counts nothing for empty text', () => {
    expect(smsSegments('')).toEqual({ encoding: 'GSM-7', characters: 0, units: 0, segments: 0, perSegment: 160 });
  });

  it('sends GSM-7 as one 160-septet message, then 153-septet parts', () => {
    expect(smsSegments(a(160))).toEqual({ encoding: 'GSM-7', characters: 160, units: 160, segments: 1, perSegment: 160 });
    expect(smsSegments(a(161))).toEqual({ encoding: 'GSM-7', characters: 161, units: 161, segments: 2, perSegment: 153 });
    expect(smsSegments(a(306)).segments).toBe(2);
    expect(smsSegments(a(307)).segments).toBe(3);
  });

  it('keeps the whole GSM 03.38 basic table (except escape) at one septet', () => {
    const basic =
      '@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !"#¤%&\'()*+,-./0123456789:;<=>?'
      + '¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà';
    expect([...basic]).toHaveLength(127);
    expect(smsSegments(basic)).toEqual({ encoding: 'GSM-7', characters: 127, units: 127, segments: 1, perSegment: 160 });
  });

  it('charges the extension table two septets each and never splits an escape pair', () => {
    expect(smsSegments('^{}\\[~]|€\f')).toMatchObject({ encoding: 'GSM-7', characters: 10, units: 20, segments: 1 });
    expect(smsSegments('€'.repeat(80))).toMatchObject({ characters: 80, units: 160, segments: 1 });
    // 152 + 2 + 152 = 306 septets: two parts by arithmetic, three when the
    // euro's escape pair cannot straddle the first boundary.
    expect(smsSegments(`${a(152)}€${a(152)}`))
      .toEqual({ encoding: 'GSM-7', characters: 305, units: 306, segments: 3, perSegment: 153 });
  });

  it('switches the whole message to UCS-2 on one non-GSM character: 70 single, 67 per part', () => {
    const curly = `${a(69)}’`;
    expect(smsSegments(curly)).toEqual({ encoding: 'UCS-2', characters: 70, units: 70, segments: 1, perSegment: 70 });
    expect(smsSegments(`${curly}a`)).toEqual({ encoding: 'UCS-2', characters: 71, units: 71, segments: 2, perSegment: 67 });
    // The middle dot the governed disclosures use is not GSM-7.
    expect(smsSegments('Summit Mortgage · NMLS #000000').encoding).toBe('UCS-2');
  });

  it('charges a surrogate pair two units and never splits it across parts', () => {
    expect(smsSegments('😀')).toEqual({ encoding: 'UCS-2', characters: 1, units: 2, segments: 1, perSegment: 70 });
    expect(smsSegments(`${a(66)}😀${a(66)}`))
      .toEqual({ encoding: 'UCS-2', characters: 133, units: 134, segments: 3, perSegment: 67 });
  });

  it('reads a governed single-segment SMS as GSM-7', () => {
    const body = 'Summit Mortgage: mortgage review. Reply YES. Msg&data rates may apply. Reply STOP to opt out.';
    expect(smsSegments(body))
      .toEqual({ encoding: 'GSM-7', characters: body.length, units: body.length, segments: 1, perSegment: 160 });
  });
});

describe('describeSmsSegments', () => {
  it('names the count, the size against the limit and the encoding', () => {
    expect(describeSmsSegments(smsSegments(a(118)))).toBe('1 segment · 118 of 160 characters · GSM-7');
    expect(describeSmsSegments(smsSegments(a(170)))).toBe('2 segments · 170 characters at 153 per segment · GSM-7');
    expect(describeSmsSegments(smsSegments(`${a(179)}’`)))
      .toBe('3 segments · 180 characters at 67 per segment · UCS-2 (non-GSM characters)');
  });

  it('never calls billed units characters: a two-unit character reports both counts and names the unit', () => {
    expect(describeSmsSegments(smsSegments('€€€'))).toBe('1 segment · 3 characters, counted as 6 of 160 septets · GSM-7');
    expect(describeSmsSegments(smsSegments(`${a(152)}€${a(152)}`)))
      .toBe('3 segments · 305 characters, counted as 306 septets at 153 per segment · GSM-7');
    expect(describeSmsSegments(smsSegments('😀')))
      .toBe('1 segment · 1 character, counted as 2 of 70 units · UCS-2 (non-GSM characters)');
    expect(describeSmsSegments(smsSegments(`${a(66)}😀${a(66)}`)))
      .toBe('3 segments · 133 characters, counted as 134 units at 67 per segment · UCS-2 (non-GSM characters)');
  });
});
