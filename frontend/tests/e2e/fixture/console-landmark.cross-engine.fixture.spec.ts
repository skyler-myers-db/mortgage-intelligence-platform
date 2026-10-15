/**
 * The Console's landmark and captions in every engine (W5d
 * w5-palette-offer-sales-loop item 5.5, a11y-05). The <aside> dropped its
 * redundant role="complementary": its aria-label alone must keep it a named
 * complementary landmark in Chromium and WebKit (this spec, cross-engine) and
 * in Firefox (forced-colors.firefox.fixture.spec.ts opens it the same way,
 * through app.openConsole). The row captions are spans that name their groups;
 * the switches keep their accessible names.
 */
import { expect, test } from './test';

test('the Console is a named complementary landmark without a role attribute; its groups keep their names', async ({ app, page }) => {
  await app.gotoRoute('/');
  const panel = await app.openConsole();
  await expect(page.getByRole('complementary', { name: 'Workspace console' })).toBeVisible();
  expect(await panel.evaluate((el) => el.getAttribute('role'))).toBeNull();
  await expect(panel.getByRole('group', { name: 'Density' })).toBeVisible();
  await expect(panel.getByRole('group', { name: 'Accent' })).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Toggle signal strength meters' })).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Single-key shortcuts' })).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Open Genie' })).toBeVisible();
});
