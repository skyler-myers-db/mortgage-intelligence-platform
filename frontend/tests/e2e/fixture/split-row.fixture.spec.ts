/**
 * The Offer detail's audited-copy card never scrolls sideways when its
 * generator-label chip plus Regenerate are wider than the card (wave-2 visual
 * gate: +17px in the pinned Linux image with the Console docked; macOS fit
 * with 4px to spare). Built from the app's real stylesheet and the real
 * markup (.surface > .offer-message-intelligence > .split-row--wrap >
 * .chip + .btn) at two widths:
 *   - just too narrow for both ends on one line: the action wraps under the
 *     label and the label keeps its full text (.split-row--wrap);
 *   - narrower than the label alone: the grid track still fits the card and
 *     the label ellipsizes (.offer-message-intelligence minmax(0, 1fr)).
 */
import { expect, test } from './test';

test('the Offer message-intelligence row fits its card when its chip and action do not fit side by side', async ({ app, page }) => {
  await app.gotoRoute('/');
  const measured = await page.evaluate(() => {
    const card = document.createElement('div');
    card.className = 'surface';
    const body = document.createElement('div');
    body.className = 'surface__body';
    const box = document.createElement('div');
    box.className = 'offer-message-intelligence';
    const row = document.createElement('div');
    row.className = 'split-row split-row--wrap';
    const chip = document.createElement('span');
    chip.className = 'chip chip--neutral';
    chip.textContent = 'Reviewed outreach template';
    const action = document.createElement('button');
    action.type = 'button';
    action.className = 'btn btn--ghost btn--sm';
    action.textContent = 'Regenerate';
    row.append(chip, action);
    box.append(row);
    body.append(box);
    card.append(body);
    card.style.inlineSize = 'max-content';
    document.querySelector('#main-content')!.append(card);
    const natural = card.getBoundingClientRect().width;
    const chipNatural = chip.getBoundingClientRect().width;
    const at = (width: number) => {
      card.style.inlineSize = `${Math.floor(width)}px`;
      return {
        card: { scrollWidth: card.scrollWidth, clientWidth: card.clientWidth },
        chip: { scrollWidth: chip.scrollWidth, clientWidth: chip.clientWidth },
        wrapped: action.getBoundingClientRect().top >= chip.getBoundingClientRect().bottom,
      };
    };
    const result = { natural, chipNatural, justTooNarrow: at(natural - 12), narrowerThanLabel: at(chipNatural - 24) };
    card.remove();
    return result;
  });
  expect(measured.natural, 'non-vacuity: the chip and action need real width').toBeGreaterThan(200);

  const { justTooNarrow, narrowerThanLabel } = measured;
  expect(justTooNarrow.card.scrollWidth, 'just too narrow: the card does not scroll sideways').toBeLessThanOrEqual(justTooNarrow.card.clientWidth);
  expect(justTooNarrow.wrapped, 'just too narrow: Regenerate wraps under the label').toBe(true);
  expect(justTooNarrow.chip.scrollWidth, 'just too narrow: the label keeps its full text').toBeLessThanOrEqual(justTooNarrow.chip.clientWidth);

  expect(narrowerThanLabel.card.scrollWidth, 'narrower than the label: the card does not scroll sideways').toBeLessThanOrEqual(narrowerThanLabel.card.clientWidth);
});
