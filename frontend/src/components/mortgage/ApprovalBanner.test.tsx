import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ApprovalBanner } from './ApprovalBanner';

/**
 * ApprovalBanner busy-state contract (R5-11, 2026-04-23).
 *
 * A rapid double-click on the Approve button used to fire `onApprove`
 * twice before the caller's async POST completed — two audit rows
 * per borrower. The banner now takes `isSubmitting`, disables both
 * buttons while true, sets `aria-busy` on the region, and swaps the
 * primary button label to "Submitting…". Vitest runs under
 * `environment: 'node'` so we pin the contract via static markup.
 */

describe('ApprovalBanner', () => {
  it('renders enabled Approve/Reject in the default state', () => {
    const html = renderToStaticMarkup(<ApprovalBanner count={3} />);
    expect(html).toContain('approval');
    expect(html).toContain('Approve outreach');
    expect(html).toContain('Reject');
    // Default aria-busy should be "false" so screen readers know the
    // region is idle.
    expect(html).toContain('aria-busy="false"');
    expect(html).not.toMatch(/<button[^>]*disabled/);
  });

  it('disables both buttons and flips aria-busy when isSubmitting=true', () => {
    const html = renderToStaticMarkup(
      <ApprovalBanner count={1} isSubmitting />,
    );
    expect(html).toContain('aria-busy="true"');
    // Label swap is the visual cue that the click registered.
    expect(html).toContain('Submitting…');
    // Both buttons (reject + approve) carry the disabled attribute.
    const disabledCount = (html.match(/\bdisabled\b/g) ?? []).length;
    expect(disabledCount).toBeGreaterThanOrEqual(2);
  });

  it('disables buttons when explicit `disabled` is true (approved/rejected terminal state)', () => {
    const html = renderToStaticMarkup(
      <ApprovalBanner count={1} disabled />,
    );
    const disabledCount = (html.match(/\bdisabled\b/g) ?? []).length;
    expect(disabledCount).toBeGreaterThanOrEqual(2);
    // `disabled` is a separate signal from `isSubmitting` — region is
    // not busy, it's simply past the decision point.
    expect(html).toContain('aria-busy="false"');
  });

  it('can block approval while leaving rejection available', () => {
    const html = renderToStaticMarkup(
      <ApprovalBanner count={1} approveDisabled />,
    );
    const buttons = [...html.matchAll(/<button([^>]*)>([\s\S]*?)<\/button>/g)];
    const reject = buttons.find((match) => match[2].includes('Reject'))?.[1] ?? '';
    const approve = buttons.find((match) => match[2].includes('Approve outreach'))?.[1] ?? '';
    expect(reject).not.toContain('disabled');
    expect(approve).toContain('disabled');
  });

  it('pluralizes the default sub-copy for count>1', () => {
    const one = renderToStaticMarkup(<ApprovalBanner count={1} />);
    expect(one).toContain('1 borrower pending review.');
    const many = renderToStaticMarkup(<ApprovalBanner count={4} />);
    expect(many).toContain('4 borrowers pending review.');
  });
});
