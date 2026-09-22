/**
 * @vitest-environment happy-dom
 *
 * 2026-09-21 audit critic-02. On the one screen where a human certifies
 * borrower-facing copy, the subject sat in a class-less browser-default
 * `<input readOnly>` that clipped it to about 24 characters ("A quick review
 * of your mor"), and the message in a disabled `<textarea>` with a resize grip
 * that scrolled past twelve lines.
 *
 * An `<input>` is a single-line box: it cannot wrap, so a long subject is
 * clipped by construction. These tests pin, on the rendered review panel, that
 * the certified copy is plain wrapping text with every character in the DOM,
 * and that the stylesheet gives that block no clip, scroll or resize.
 *
 * What happy-dom cannot prove is the painted result (no layout engine): the
 * pixel-level "nothing is cut off at 1440x900" check belongs to the e2e lane.
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
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true }),
}));

import { OfferReviewGrid } from './offer-orchestrator.panels';
import type { OutreachChannel } from './offer-orchestrator.constants';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// 116 characters (the old input's maxLength was 120): about five times what it showed.
const LONG_SUBJECT =
  'A quick review of your mortgage options now that rates moved: what a licensed loan officer can walk through with you';
const LONG_BODY = Array.from(
  { length: 18 },
  (_, line) => `Paragraph ${line + 1}: a licensed loan officer can review the available options with you.`,
).join('\n\n');

describe('Offer review — the copy being certified', () => {
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

  function renderPanel(overrides: { channel?: OutreachChannel; loaded?: boolean; saving?: boolean } = {}): void {
    act(() => root.render(
      <MemoryRouter>
        <OfferReviewGrid
          borrower={null}
          borrowerId="B-TEST1"
          recommendation={null}
          productLabel="Home-equity review"
          leadIsSaved={false}
          saveCurrentLead={vi.fn()}
          draftWarming={null}
          draftLoaded={overrides.loaded ?? true}
          draftError={null}
          draftSubject={LONG_SUBJECT}
          draftText={LONG_BODY}
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

  const subject = () => container.querySelector<HTMLElement>('[data-testid="outreach-subject"]');
  const body = () => container.querySelector<HTMLElement>('[data-testid="outreach-draft"]');

  it('renders the whole subject as wrapping text, not a single-line input that clips it', () => {
    renderPanel();

    expect(subject()?.textContent).toBe(LONG_SUBJECT);
    // The defect was the element type: a text input cannot wrap.
    expect(subject()?.tagName).toBe('DIV');
    expect(container.querySelector('input[data-testid="outreach-subject"]')).toBeNull();
    expect(subject()?.classList.contains('route-textarea--readout')).toBe(true);
    expect(subject()?.classList.contains('route-textarea--subject')).toBe(true);
    // Still named for assistive tech and for the live e2e locator.
    expect(subject()?.getAttribute('role')).toBe('group');
    expect(subject()?.getAttribute('aria-label')).toBe('Outreach subject — review only');
    expect(container.textContent).toContain('Subject');
  });

  it('renders the whole message as a read-only block: no textarea, nothing to type into or drag', () => {
    renderPanel();

    expect(body()?.textContent).toBe(LONG_BODY);
    expect(body()?.tagName).toBe('DIV');
    expect(container.querySelector('textarea[data-testid="outreach-draft"]')).toBeNull();
    expect(container.querySelector('[data-testid="outreach-draft"] textarea, [contenteditable]')).toBeNull();
    expect(body()?.classList.contains('route-textarea--readout')).toBe(true);
    expect(body()?.getAttribute('aria-label')).toBe('Outreach draft — review only');
    // The posture label the approver relies on is unchanged.
    expect(container.textContent).toContain('Governed outreach · exact audited copy');
    expect(container.textContent).toContain('Review the exact audited copy before approval');
  });

  it('drops the subject for SMS and keeps the message', () => {
    renderPanel({ channel: 'sms' });

    expect(subject()).toBeNull();
    expect(body()?.textContent).toBe(LONG_BODY);
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

  it('styles the read-out so nothing clips, scrolls or resizes, and newlines survive', () => {
    const css = readFileSync(
      join(process.cwd(), 'src', 'routes', 'offer-orchestrator.review-copy.css'),
      'utf8',
    );
    // Two classes: out-ranks the single-class base rules (`.route-textarea`
    // caps the height at 12lh, scrolls and is resizable) whichever sheet
    // loads first. The route sheet ships with the lazy route chunk.
    const rule = /\.route-textarea\.route-textarea--readout\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';

    expect(rule).toMatch(/resize:\s*none/);
    expect(rule).toMatch(/max-height:\s*none/);
    expect(rule).toMatch(/overflow:\s*visible/);
    expect(rule).toMatch(/white-space:\s*pre-wrap/);
    expect(rule).toMatch(/overflow-wrap:\s*anywhere/);
  });
});
