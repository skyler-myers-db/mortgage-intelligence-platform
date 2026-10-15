/**
 * W5e w5-compiler-lint proof P3 (audit critic-08, migration rule R5): a
 * natively disabled button whose disabled REASON moved from a native title=
 * to the design-system tooltip still tells a pointer user why. Hovering the
 * Offer gate's disabled Approve (a non-approver: the approver gate) opens
 * #mip-tooltip with the reason, in Chromium and in WebKit (fixture-webkit
 * in the e2e-cross-engine CI job; Firefox runs only the forced-colors
 * specs). The reason is also visible text in the gate, so an engine that
 * sends a disabled control no pointer events would still leave it readable.
 */
import type { SessionResponse } from '../../../src/types';
import { PRIMARY_BORROWER } from './data/borrowers';
import { NON_APPROVER_SESSION } from './data/approvalRequests';
import { json } from './mockApi';
import { expect, test } from './test';
import { TOOLTIP_POPUP, tooltipText } from './tooltip';

test.use({ viewport: { width: 1440, height: 900 } });

test('a disabled Approve shows its approver-gate reason in #mip-tooltip on hover, with no native title', async ({ app, page, mockApi }) => {
  mockApi.register<SessionResponse>('GET', '/api/session', () => json<SessionResponse>(NON_APPROVER_SESSION));
  await app.gotoRoute(`/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`);
  const gate = page.getByTestId('offer-action-bar').locator('.approval');
  const reason = gate.getByTestId('approval-gate-reason');
  await expect(reason).toContainText('Requires approver role');
  const approve = gate.getByRole('button', { name: 'Approve outreach' });
  await expect(approve).toBeDisabled();
  await expect(approve).not.toHaveAttribute('title', /.*/);
  // The tooltip carries the gate string the banner was given (approverGate.ts).
  expect(await tooltipText(approve)).toBe('Requires approver role');

  // The controller loads after the first paint; its install creates the popup.
  await expect(page.locator(TOOLTIP_POPUP)).toBeAttached();
  await approve.hover();
  const popup = page.locator(`${TOOLTIP_POPUP}[role="tooltip"]:not([hidden])`);
  await expect(popup).toBeVisible();
  await expect(popup).toContainText('Requires approver role');
  await expect(approve).toBeDisabled();
});
