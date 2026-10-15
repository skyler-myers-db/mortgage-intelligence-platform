/**
 * Buyer-language route copy (2026-09-21 audit flow-10), linted where it
 * renders: every string of the Segments copy module (each input it takes)
 * and the Portfolio save guard in its three states (ready, over the limit,
 * unverified), against the shared plumbing-word list
 * (src/test/routeCopyBans.ts) plus the set algebra Segments used to speak.
 * Glossary tooltips (.glossary-term__tip) are exempt: the exact set
 * definitions live there on purpose.
 */
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { ROUTE_COPY_BANS, SET_ALGEBRA_BANS } from '../test/routeCopyBans';
import type { PortfolioPreview } from '../types';
import { CampaignBuildGuard } from './portfolio-builder.governance';
import { SEGMENT_MODE_OPTIONS } from './segment-intelligence.filters';
import {
  rankedHeaderModeSuffix,
  rankedScopeCopy,
  SEGMENT_MODE_HELP,
  segmentCountScopeCopy,
  segmentFilterHint,
  segmentsLede,
} from './segment-intelligence.copy';

const BANS = [...ROUTE_COPY_BANS, ...SET_ALGEBRA_BANS];

function offenders(text: string): string[] {
  return BANS.filter((ban) => ban.test(text)).map(String);
}

function segmentsCopy(): string[] {
  const out: string[] = [...Object.values(SEGMENT_MODE_HELP)];
  for (const option of SEGMENT_MODE_OPTIONS) out.push(option.label, option.description);
  for (const selected of [true, false]) {
    out.push(segmentsLede(selected), rankedScopeCopy(selected));
    for (const mode of ['any', 'all'] as const) out.push(segmentCountScopeCopy(selected, mode));
  }
  for (const mode of ['any', 'all'] as const) out.push(segmentFilterHint(mode), rankedHeaderModeSuffix(mode));
  return out;
}

function guardText(preview: Partial<PortfolioPreview>): string {
  const html = renderToStaticMarkup(
    <MemoryRouter>
      <CampaignBuildGuard preview={preview as PortfolioPreview} />
    </MemoryRouter>,
  );
  return html.replace(/<span[^>]*class="glossary-term__tip"[^>]*>.*?<\/span>/g, '').replace(/<[^>]+>/g, ' ');
}

const GUARD_STATES: ReadonlyArray<[string, Partial<PortfolioPreview>]> = [
  ['ready', { campaign_build_eligible: true, campaign_build_contact_count: 7973, campaign_build_limit: 10_000 }],
  ['over the limit', { campaign_build_eligible: false, campaign_build_contact_count: 10_001, campaign_build_limit: 10_000 }],
  ['unverified', { campaign_build_eligible: false, campaign_build_contact_count: null, campaign_build_limit: 10_000 }],
];

describe('route copy lint (flow-10)', () => {
  it('the ban list bites (non-vacuity)', () => {
    expect(offenders('Server treatment preflight')).not.toEqual([]);
    expect(offenders('Any selected is a de-duplicated OR cohort')).not.toEqual([]);
    expect(offenders('All selected is the AND intersection')).not.toEqual([]);
  });

  it('every Segments copy string is in lender words', () => {
    const copy = segmentsCopy();
    expect(copy.length).toBeGreaterThan(15);
    for (const text of copy) expect(offenders(text), text).toEqual([]);
  });

  it('keeps the Segments lede short enough for one line at 1440 with the Console open', () => {
    // .proto-hero .lede caps at 640px; Linux draws Geist ~6% wider than macOS.
    for (const selected of [true, false]) expect(segmentsLede(selected).length).toBeLessThanOrEqual(80);
  });

  it.each(GUARD_STATES)('the Portfolio save guard, %s, is in buyer words', (_label, preview) => {
    const text = guardText(preview);
    expect(text).toMatch(/saved campaign/);
    expect(offenders(text), text).toEqual([]);
    expect(text).not.toMatch(/\btreatment\b/i);
  });
});
