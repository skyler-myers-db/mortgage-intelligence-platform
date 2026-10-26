import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { outreachMutationKeys } from '../../lib/mutations/outreach';
import { APPROVE_OUTREACH_LABEL, ApprovalBanner } from './ApprovalBanner';

const BORROWER = 'B-0000000000001';

/** A client whose cache holds a decision on the wire for `borrowerId` (never settles). */
function clientWithPending(decision: 'approve' | 'reject', borrowerId: string): QueryClient {
  const client = new QueryClient();
  const mutation = client.getMutationCache().build(client, {
    mutationKey: decision === 'approve' ? outreachMutationKeys.approve : outreachMutationKeys.reject,
    mutationFn: () => new Promise(() => undefined),
  });
  void mutation.execute({ decision, borrowerId, requestId: 'req-1', body: {} });
  return client;
}

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
  it('has one approve label: the overrides are gone (critic-11)', () => {
    expect(APPROVE_OUTREACH_LABEL).toBe('Approve outreach');
    // @ts-expect-error approveLabel was removed: every banner reads APPROVE_OUTREACH_LABEL.
    const html = renderToStaticMarkup(<ApprovalBanner count={1} approveLabel="Approve" rejectLabel="Drop" />);
    expect(html).toContain('>Approve outreach<');
    expect(html).not.toContain('>Approve<');
    expect(html).not.toContain('Drop');
  });

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

  // Audit flow-02 / shell-06 (2026-09-21): the banner was identity- and
  // role-blind. Non-approvers keep a VISIBLE gate: disabled, with a reason.
  it('prints whose approval this is for an approver', () => {
    const html = renderToStaticMarkup(
      <ApprovalBanner count={1} actorEmail="approver.one@summit.example" />,
    );
    expect(html).toContain('Approving as');
    expect(html).toContain('approver.one@summit.example');
    expect(html).not.toMatch(/<button[^>]*disabled/);
    expect(html).not.toContain('Requires approver role');
  });

  it('stays visible but disables both buttons, with the described reason, for a non-approver', () => {
    const html = renderToStaticMarkup(
      <ApprovalBanner
        count={1}
        approverGate="Requires approver role"
        actorEmail="analyst@summit.example"
      />,
    );
    expect(html).toContain('Human approval required before outreach');
    expect(html).not.toContain('Approving as');
    const reasonId = html.match(/<div class="approval__sub" id="([^"]+)"[^>]*>Requires approver role/)?.[1];
    expect(reasonId).toBeTruthy();
    const buttons = [...html.matchAll(/<button([^>]*)>/g)].map((match) => match[1]);
    expect(buttons).toHaveLength(2);
    for (const attrs of buttons) {
      expect(attrs).toContain('disabled');
      // The visible reason first, then the Tooltip's description (critic-08: no native title).
      expect(attrs).toMatch(new RegExp(`aria-describedby="${reasonId} tooltip-desc[^"]+"`));
      expect(attrs).toContain('data-tooltip=');
      expect(attrs).not.toContain('title=');
    }
  });

  it('prints no identity line when the edge forwarded none', () => {
    const html = renderToStaticMarkup(<ApprovalBanner count={1} />);
    expect(html).not.toContain('Approving as');
  });

  it('pluralizes the default sub-copy for count>1', () => {
    const one = renderToStaticMarkup(<ApprovalBanner count={1} />);
    expect(one).toContain('1 borrower pending review.');
    const many = renderToStaticMarkup(<ApprovalBanner count={4} />);
    expect(many).toContain('4 borrowers pending review.');
  });

  describe('a decision in flight from any surface (stack-09 item 5)', () => {
    const banner = (client: QueryClient, borrowerId: string | null = BORROWER) => renderToStaticMarkup(
      <QueryClientProvider client={client}>
        <ApprovalBanner borrowerId={borrowerId} onApprove={() => undefined} onReject={() => undefined} />
      </QueryClientProvider>,
    );

    it('a pending approve for this borrower: busy, both buttons disabled, one status line', () => {
      const html = banner(clientWithPending('approve', BORROWER));
      expect(html).toContain('aria-busy="true"');
      expect((html.match(/<button[^>]*disabled/g) ?? []).length).toBe(2);
      expect(html).toMatch(/<div class="approval__sub" role="status" data-testid="approval-pending">Approval being recorded…<\/div>/);
      // Pessimistic: never an approved state before the write returns.
      expect(html).not.toMatch(/\bApproved\b/);
    });

    it('a pending reject reads as a rejection being recorded', () => {
      expect(banner(clientWithPending('reject', BORROWER))).toContain('Rejection being recorded…');
    });

    it('another borrower\'s decision, or none, leaves the banner idle', () => {
      const idle = banner(clientWithPending('approve', 'B-0000000000002'));
      expect(idle).toContain('aria-busy="false"');
      expect(idle).not.toMatch(/<button[^>]*disabled/);
      expect(idle).toMatch(/<div class="sr-only" role="status" data-testid="approval-pending"><\/div>/);
      expect(banner(clientWithPending('approve', BORROWER), null)).toContain('aria-busy="false"');
    });

    it('renders idle without a QueryClientProvider (the inert fallback client)', () => {
      const html = renderToStaticMarkup(<ApprovalBanner borrowerId={BORROWER} />);
      expect(html).toContain('aria-busy="false"');
      expect(html).toContain(APPROVE_OUTREACH_LABEL);
    });
  });
});
