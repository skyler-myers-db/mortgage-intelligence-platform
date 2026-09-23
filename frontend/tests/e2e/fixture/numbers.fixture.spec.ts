/**
 * Rendered-layer proofs for the wave-0 number fixes (audit flow-v1 banner
 * reconciliation, dataviz-v1 / dataviz-03 funnel labels, quality-04 null
 * LTV, visual-v2 story opener).
 *
 * The fixtures agree by construction (data/reference.ts): the contactable
 * in-the-money count Home's banner states is what the `itm` segment and the
 * `/lead-queue?segment=itm` footer report.
 */
import type { Borrower360 } from '../../../src/types';
import { PRIMARY_BORROWER } from './data/borrowers';
import { TOTALS } from './data/reference';
import { expect, test } from './test';

const fmt = (value: number) => value.toLocaleString('en-US');

test.describe('Home approval-queue banner', () => {
  test('states N contactable of M, and N is the queue total its link opens', async ({ app, page }) => {
    await app.gotoRoute('/');
    const banner = page.getByRole('region', { name: 'Approval queue' });
    await expect(banner.getByTestId('approval-queue-contactable')).toHaveText(fmt(TOTALS.contactableInTheMoney));
    await expect(banner.getByTestId('approval-queue-screen')).toHaveText(fmt(TOTALS.inTheMoney));
    await expect(banner).toContainText(
      `${fmt(TOTALS.contactableInTheMoney)} contactable of ${fmt(TOTALS.inTheMoney)} borrowers passing the refinance-economics screen`,
    );
    // M is the same figure the KPI card above it renders.
    await expect(page.locator('.kpi', { hasText: 'Refi economics screen' }).locator('.kpi__value')).toHaveText(fmt(TOTALS.inTheMoney));

    const link = banner.getByRole('link', { name: 'Open review queue' });
    await expect(link).toHaveAttribute('href', '/lead-queue?segment=itm');
    await link.click();
    await expect(page).toHaveURL(/\/lead-queue\?segment=itm$/);
    await app.settle();
    await expect(page.locator('.surface__ft').first()).toContainText(`of ${fmt(TOTALS.contactableInTheMoney)} total matching filters`);
  });
});

test.describe('Executive activation funnel', () => {
  test('labels every stage as a share of addressable and carries a conversion only for Approved → Actioned', async ({ app, page }) => {
    await app.gotoRoute('/analytics');
    const funnel = page.locator('.funnel-sankey');
    await expect(funnel).toBeVisible();
    const shares = funnel.locator('.funnel-sankey__conv[data-ratio="share-of-addressable"]');
    const conversions = funnel.locator('.funnel-sankey__conv[data-ratio="nested-conversion"]');
    const nodes = funnel.locator('.funnel-sankey__node');
    await expect(nodes).not.toHaveCount(0);
    // Every stage but Addressable itself states its share of addressable.
    await expect(shares).toHaveCount((await nodes.count()) - 1);
    for (const text of await shares.allTextContents()) expect(text).toMatch(/^\d+(\.\d+)?% of addressable$/);
    // One nested pair, and it is the only conversion on the chart.
    await expect(conversions).toHaveCount(1);
    await expect(conversions).toHaveText(/^\d+(\.\d+)?% of approved$/);
    const actioned = nodes.filter({ has: page.locator('.funnel-sankey__label', { hasText: /^Actioned$/ }) });
    await expect(actioned.locator('[data-ratio="nested-conversion"]')).toHaveCount(1);
    await expect(funnel.getByText(/conversion/i)).toHaveCount(0);
    await expect(funnel).toHaveAttribute('aria-label', /not a conversion from the stage before it/);
  });
});

test.describe('Borrower 360', () => {
  test('a withheld LTV renders an accessible unknown, never the text "null"', async ({ app, mockApi, page }) => {
    const withheld: Borrower360 = { ...PRIMARY_BORROWER, ltv: null, ltv_basis_is_unreliable: true, equity_estimate: 0 };
    mockApi.register<Borrower360>('GET', '/api/borrowers/:id', () => ({ body: withheld }));
    await app.gotoRoute(`/borrower-360/${PRIMARY_BORROWER.borrower_id}`);
    const main = page.locator('#main-content');
    await expect(main).not.toContainText('null');
    // The field is a label + value pair; locate the value by its sr-only name.
    const unknown = main.locator('.field__value', { has: page.locator('.sr-only', { hasText: 'LTV and equity unknown' }) });
    await expect(unknown).toHaveCount(1);
    await expect(unknown.locator('[aria-hidden="true"]')).toHaveText('—');
    // Field = <div><label/><div><value/><subs/></div></div>: two levels up.
    const field = unknown.locator('xpath=../..');
    await expect(field.locator('.field__label')).toContainText('LTV');
    await expect(field).toContainText('LTV basis unreliable');
    await expect(field).toContainText('Withheld, not zero');
  });

  test('a single-property borrower story opens with a full sentence', async ({ app, page }) => {
    expect(PRIMARY_BORROWER.related_property_count).toBe(1);
    await app.gotoRoute(`/borrower-360/${PRIMARY_BORROWER.borrower_id}`);
    const narrative = page.locator('.borrower-story__narrative');
    await expect(narrative).toBeVisible();
    const text = (await narrative.textContent()) ?? '';
    const subject = `This ${PRIMARY_BORROWER.city}, ${PRIMARY_BORROWER.state} owner-occupant`;
    expect(text.startsWith(subject)).toBe(true);
    // The subject takes the economics as its predicate: a verb follows it.
    expect(text.slice(subject.length)).toMatch(/^ (carries a|is |has )/);
    expect(text.startsWith(`${subject}.`)).toBe(false);
    expect(text).toMatch(/^This [^.]+ carries a \d+\.\d{2}% rate, \d+ bps above market, with \d+% equity on a \$[\d.]+[KM] lien\. Primary offer: /);
  });
});
