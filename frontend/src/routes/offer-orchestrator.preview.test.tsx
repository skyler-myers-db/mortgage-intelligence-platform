/**
 * @vitest-environment happy-dom
 *
 * 2026-09-21 audit critic-02. On the one screen where a human certifies
 * borrower-facing copy, the subject sat in a class-less browser-default
 * `<input readOnly>` that clipped it to about 24 characters ("A quick review
 * of your mor"), and the message in a disabled `<textarea>`. Wave 0 made both
 * plain wrapping text; part 2 frames that text the way the borrower receives
 * it: an email / letter frame (From, To as the synthetic contact, a wrapping
 * Subject, the body's paragraphs, the disclosure) and an SMS bubble with its
 * carrier segment count.
 *
 * These tests pin, on the rendered review panel, that the certified copy is
 * read-only text with every character in the DOM and the paragraphs joined
 * back giving exactly the audited body. The painted result at 1440x900 (no
 * clipping, both themes) is proven in
 * tests/e2e/fixture/offer-orchestrator.fixture.spec.ts.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the route-local stylesheet as text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';

declare const process: { cwd(): string };

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true, lender: 'Summit Mortgage' }),
}));

import { OfferReviewGrid } from './offer-orchestrator.panels';
import { copyParagraphs } from './offer-orchestrator.preview';
import type { OutreachChannel } from './offer-orchestrator.constants';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BORROWER_ID = 'B-0000000000001';
// 116 characters (the old input's maxLength was 120): about five times what it showed.
const LONG_SUBJECT =
  'A quick review of your mortgage options now that rates moved: what a licensed loan officer can walk through with you';
const LONG_BODY = Array.from(
  { length: 18 },
  (_, line) => `Paragraph ${line + 1}: a licensed loan officer can review the available options with you.`,
).join('\n\n');
const SMS_BODY = 'Summit Mortgage: mortgage review. Reply YES. Msg&data rates may apply. Reply STOP to opt out.';

describe('Offer review — the certified copy preview', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function renderPanel(
    overrides: { channel?: OutreachChannel; body?: string; loaded?: boolean; saving?: boolean } = {},
  ): void {
    act(() => root.render(
      <MemoryRouter>
        <OfferReviewGrid
          borrower={null}
          borrowerId={BORROWER_ID}
          recommendation={null}
          productLabel="Home-equity review"
          leadIsSaved={false}
          saveCurrentLead={vi.fn()}
          draftWarming={null}
          draftLoaded={overrides.loaded ?? true}
          draftError={null}
          draftSubject={overrides.channel === 'sms' ? '' : LONG_SUBJECT}
          draftText={overrides.body ?? LONG_BODY}
          draftChannel={overrides.channel ?? 'email'}
          draftProofFresh
          onDraftChannelChange={vi.fn()}
          approving={false}
          draftDisclosureVersion="v1"
          draftDisclosureState="IL"
          draftGeneratorLabel={null}
          draftGenerationMode={null}
          draftStrategy={null}
          draftEvidence={[]}
          draftEvidenceAssets={[]}
          regenerateDraft={vi.fn()}
          draftIsSaved={false}
          saveCurrentDraft={vi.fn()}
          draftSavePending={overrides.saving ?? false}
          savedDraftExists={false}
          resetCurrentDraft={vi.fn()}
          draftReady
        />
      </MemoryRouter>,
    ));
  }

  const frame = () => container.querySelector<HTMLElement>('article[data-testid="certified-copy"]');
  const subject = () => container.querySelector<HTMLElement>('[data-testid="outreach-subject"]');
  const body = () => container.querySelector<HTMLElement>('[data-testid="outreach-draft"]');
  /** The rendered paragraphs joined back the way the body was split. */
  const bodyText = () => [...(body()?.querySelectorAll('p') ?? [])].map((p) => p.textContent).join('\n\n');
  /** dt label -> dd text of the frame header. */
  const header = () => Object.fromEntries(
    [...(frame()?.querySelectorAll('dl > div') ?? [])].map((row) => [
      row.querySelector('dt')?.textContent,
      row.querySelector('dd')?.textContent,
    ]),
  );

  it('frames an email: From the lender, To the synthetic contact, a wrapping Subject', () => {
    renderPanel();

    expect(frame()?.getAttribute('aria-label')).toBe('Certified email preview');
    expect(frame()?.querySelector('dl')).not.toBeNull();
    expect(header()).toEqual({
      From: 'Summit Mortgage',
      To: `${BORROWER_ID}Synthetic contact`,
      Subject: LONG_SUBJECT,
    });
    // The subject is plain wrapping text, never a single-line input.
    expect(subject()?.textContent).toBe(LONG_SUBJECT);
    expect(subject()?.tagName).toBe('DIV');
    expect(subject()?.classList.contains('certified-copy__subject')).toBe(true);
    expect(container.querySelector('input, textarea, [contenteditable]')).toBeNull();
    // Still named for assistive tech and for the live e2e locator.
    expect(subject()?.getAttribute('role')).toBe('group');
    expect(subject()?.getAttribute('aria-label')).toBe('Outreach subject — review only');
  });

  it('renders the whole body as paragraphs that join back to exactly the audited copy', () => {
    renderPanel();

    expect(body()?.querySelectorAll('p')).toHaveLength(18);
    expect(bodyText()).toBe(LONG_BODY);
    expect(body()?.getAttribute('aria-label')).toBe('Outreach draft — review only');
    // The posture label the approver relies on is unchanged.
    expect(container.textContent).toContain('Governed outreach · exact audited copy');
    expect(container.textContent).toContain('Review the exact audited copy before approval');
  });

  it('splits on blank lines only, so odd spacing survives exactly', () => {
    const odd = '  Hello,\n\n\nLine one\nline two  \n\n\n\nSummit Mortgage · NMLS #000000\n';
    expect(copyParagraphs(odd).join('\n\n')).toBe(odd);
    renderPanel({ body: odd });
    expect(bodyText()).toBe(odd);
  });

  it('shows the disclosure the draft was generated under in the frame footer', () => {
    renderPanel();

    const footer = frame()?.querySelector('footer');
    expect(footer?.textContent).toBe('Disclosure v1 · IL');
    // Once, in the frame: not repeated in the channel row.
    expect(container.textContent?.match(/Disclosure v1/g)).toHaveLength(1);
  });

  it('frames direct mail as a letter with its subject', () => {
    renderPanel({ channel: 'direct_mail' });

    expect(frame()?.getAttribute('aria-label')).toBe('Certified letter preview');
    expect(header().Subject).toBe(LONG_SUBJECT);
    expect(container.querySelector('[data-testid="sms-segments"]')).toBeNull();
  });

  it('shows SMS as a bubble with its segment count and no subject', () => {
    renderPanel({ channel: 'sms', body: SMS_BODY });

    expect(frame()?.getAttribute('aria-label')).toBe('Certified SMS preview');
    expect(subject()).toBeNull();
    expect(Object.keys(header())).toEqual(['From', 'To']);
    expect(body()?.classList.contains('certified-copy__bubble')).toBe(true);
    expect(bodyText()).toBe(SMS_BODY);
    expect(container.querySelector('[data-testid="sms-segments"]')?.textContent)
      .toBe(`1 segment · ${SMS_BODY.length} of 160 characters · GSM-7`);
  });

  it('counts a non-GSM SMS in UCS-2 segments', () => {
    const curly = `${SMS_BODY} We’re here to help.`;
    renderPanel({ channel: 'sms', body: curly });

    expect(container.querySelector('[data-testid="sms-segments"]')?.textContent)
      .toBe(`2 segments · ${curly.length} characters at 67 per segment · UCS-2 (non-GSM characters)`);
  });

  it('marks the copy not-current while the draft is loading or saving, and current otherwise', () => {
    renderPanel();
    expect(subject()?.hasAttribute('aria-disabled')).toBe(false);
    expect(body()?.hasAttribute('aria-disabled')).toBe(false);

    renderPanel({ loaded: false });
    expect(subject()?.getAttribute('aria-disabled')).toBe('true');
    expect(body()?.getAttribute('aria-disabled')).toBe('true');

    renderPanel({ saving: true });
    expect(body()?.getAttribute('aria-disabled')).toBe('true');
  });

  it('styles the frame so nothing clips, scrolls or resizes, and spacing survives', () => {
    const css = readFileSync(join(process.cwd(), 'src', 'routes', 'offer-orchestrator.preview.css'), 'utf8');
    const rules = css.replace(/\/\*[\s\S]*?\*\//g, '');
    const rule = (selector: string) =>
      new RegExp(`${selector.replace(/[.[\]"=]/g, '\\$&')}\\s*\\{([^}]*)\\}`).exec(rules)?.[1] ?? '';

    for (const selector of ['.certified-copy__subject', '.certified-copy__para']) {
      expect(rule(selector), selector).toMatch(/white-space:\s*pre-wrap/);
      expect(rule(selector), selector).toMatch(/overflow-wrap:\s*anywhere/);
    }
    // No rule in the sheet can cut the copy off or give it an inner scroller.
    expect(rules).not.toMatch(/(?<![-\w])overflow(?:-[xy]|-block|-inline)?:\s*(?:hidden|auto|scroll|clip)/);
    expect(rules).not.toMatch(/max-(?:height|block-size)|text-overflow|line-clamp|(?<![-\w])resize:/);
  });
});
