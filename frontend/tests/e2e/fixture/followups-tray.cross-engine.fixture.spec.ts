/**
 * The Follow-ups tray in another engine (W5d w5-followups-snooze;
 * deviation:followups-tray). fixture-webkit runs only *.cross-engine and
 * *.webkit specs: the tray is a manual popover in the top layer, so at
 * 1152x720, with the Console as a bottom sheet, the point under the tray's
 * centre is still the tray; Escape closes it onto its trigger.
 */
import { registerFollowUps } from './data/followUps';
import { expect, test } from './test';

const TRIGGER = '.rail button.rail__item:has(.mod:text-is("Due"))';

test('the tray stays on top of the Console sheet and Escape returns to its trigger', async ({ app, mockApi, page }) => {
  registerFollowUps(mockApi);
  await page.setViewportSize({ width: 1152, height: 720 });
  await app.gotoRoute('/');
  await app.openConsole();

  // The bottom sheet covers the rail's lower items: reach the trigger by keyboard.
  const trigger = page.locator(TRIGGER);
  await trigger.focus();
  await page.keyboard.press('Enter');
  const tray = page.getByRole('region', { name: 'Follow-ups' });
  await expect(tray.locator('.followups__row').first()).toBeVisible();
  await app.settle();

  const onTop = await tray.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return hit !== null && element.contains(hit);
  });
  expect(onTop, 'the top-layer tray, not the Console sheet, is under its centre').toBe(true);

  await page.keyboard.press('Escape');
  await expect(tray).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
});
