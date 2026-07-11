import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CampaignPrefillBanner } from './CampaignPrefillBanner';
import { makeCampaignPrefill } from '../../lib/campaignPrefill';

describe('CampaignPrefillBanner', () => {
  it('renders nothing without a prefill or error', () => {
    expect(renderToStaticMarkup(<CampaignPrefillBanner prefill={null} />)).toBe('');
  });

  it('renders the geo draft context with honest S10 boundary copy', () => {
    const prefill = makeCampaignPrefill({
      level: 'zip',
      state: 'IL',
      countyFips: '17031',
      // Display-ready name, exactly as the map's county selection carries it.
      countyName: 'Cook County',
      zip: '60611',
      segmentCodes: ['itm'],
      segmentMode: 'any',
      leadCount: 64,
      unattendedCount: 43,
    });
    const html = renderToStaticMarkup(<CampaignPrefillBanner prefill={prefill} />);
    expect(html).toContain('Campaign draft from geography drill-down');
    expect(html).toContain('State IL — applied');
    expect(html).toContain('Cook County · FIPS 17031');
    expect(html).toContain('ZIP 60611 — draft context');
    // Honest copy: county/ZIP + segments are context until S10 ships.
    expect(html).toContain('when the campaign builder (S10) ships');
    // Overlay snapshot counts are segment-agnostic; the qualifier is pinned
    // so the counts can never read as segment-filtered next to the segment chips.
    expect(html).toContain('64 leads · 43 unattended at draft time (all segments)');
  });

  it('renders a single honest line for a malformed marked link', () => {
    const html = renderToStaticMarkup(
      <CampaignPrefillBanner prefill={null} error="county_fips must be a 5-digit county FIPS code" />,
    );
    expect(html).toContain('Ignored an invalid campaign prefill link.');
    expect(html).not.toContain('Campaign draft from geography drill-down');
  });
});
