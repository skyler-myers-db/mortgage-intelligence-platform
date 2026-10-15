/**
 * The Audit explorer header (W5d VRT review, audit-ledger--{dark,light}--console).
 * W5d (tables-10) put five items beside the header's title block: Copy table
 * context, Exclude my ledger reads, Page CSV, Export PDF and the status chip.
 * With the Console open at 1440x900 they no longer fitted: the shared
 * .chip-row wrapped and left the 'page 1 · 12 rows' chip alone, left-aligned,
 * under Copy table context, with line 1 ending short of the header's edge.
 *
 * What a reader must see, at 1440x900 and 1280x720, both themes, Console open
 * and closed: the actions and the status chip as ONE group, on one line,
 * ending at the header's inner right edge with the chip last, nothing over
 * the title block, nothing truncated. At 1440x900 the group still sits beside
 * the title (the title block's subtitle wraps instead); a narrower header
 * moves the whole group under the title rather than splitting it.
 *
 * Linux Chromium draws Geist Mono about 6% wider (the LINUX_TEXT_EMULATION
 * pattern of charts.fixture.spec.ts): off Linux the header's text is widened
 * past that, +0.5px per glyph over each item's own tracking, so a group that
 * only fits at macOS widths fails here too.
 */
import type { Page } from '@playwright/test';
import type { FixtureTheme } from './app';
import { expect, test } from './test';

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const VIEWPORTS = [{ width: 1440, height: 900 }, { width: 1280, height: 720 }] as const;
const HEADER = '#audit > .surface__hdr';

const LINUX_TEXT_EMULATION = `
  ${HEADER} .chip { letter-spacing: calc(0.02em + 0.5px); }
  ${HEADER} .btn, ${HEADER} .audit-exclude-own { letter-spacing: calc(var(--tracking-body, 0px) + 0.5px); }
`;

async function setConsole(page: Page, open: boolean): Promise<void> {
  const body = page.getByRole('complementary', { name: 'Workspace console' }).locator('.tweaks__body');
  if ((await body.isVisible().catch(() => false)) !== open) {
    await page.getByRole('banner').getByRole('button', { name: 'Toggle console' }).click();
  }
  if (open) await expect(body).toBeVisible();
  else await expect(body).toBeHidden();
  // The shell's column change is a transition: measure the settled layout.
  await page.evaluate(async () => {
    await Promise.all(document.getAnimations().map((animation) => animation.finished.catch(() => undefined)));
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
}

interface HeaderGeometry {
  /** The group's items, by visual line, top to bottom. */
  lines: string[][];
  /** Each line's right edge minus the header's inner right edge (0 = end-aligned). */
  lineEnds: number[];
  /** The last item of the last line. */
  last: string;
  /** The group's first line shares the title block's row. */
  besideTitle: boolean;
  overTitle: string[];
  outsideHeader: string[];
  truncated: string[];
}

async function headerGeometry(page: Page): Promise<HeaderGeometry> {
  return page.locator(HEADER).evaluate((header) => {
    const group = header.querySelector(':scope > .chip-row');
    const title = header.firstElementChild?.getBoundingClientRect();
    const name = (element: Element) => element.textContent?.trim() || element.className;
    if (!group || !title) {
      return { lines: [], lineEnds: [], last: 'no action group', besideTitle: false, overTitle: [], outsideHeader: [], truncated: [] };
    }
    const style = getComputedStyle(header);
    const box = header.getBoundingClientRect();
    const innerLeft = box.left + Number.parseFloat(style.borderLeftWidth) + Number.parseFloat(style.paddingLeft);
    const innerRight = box.right - Number.parseFloat(style.borderRightWidth) - Number.parseFloat(style.paddingRight);
    const items = [...group.children].map((element) => ({ element, rect: element.getBoundingClientRect() }));
    // One visual line = items whose vertical centres fall inside the line's
    // first item (a 28px button and a 24px chip are centred on one line).
    const lines: Array<typeof items> = [];
    for (const item of [...items].sort((a, b) => a.rect.top - b.rect.top || a.rect.left - b.rect.left)) {
      const centre = (item.rect.top + item.rect.bottom) / 2;
      const line = lines.find((candidate) => centre > candidate[0].rect.top && centre < candidate[0].rect.bottom);
      if (line) line.push(item);
      else lines.push([item]);
    }
    lines.forEach((line) => line.sort((a, b) => a.rect.left - b.rect.left));
    const lastLine = lines.at(-1) ?? [];
    const firstLine = lines[0] ?? [];
    const overlaps = (rect: DOMRect) =>
      rect.left < title.right - 0.5 && rect.right > title.left + 0.5 && rect.top < title.bottom - 0.5 && rect.bottom > title.top + 0.5;
    return {
      lines: lines.map((line) => line.map((item) => name(item.element))),
      lineEnds: lines.map((line) => Math.round((Math.max(...line.map((item) => item.rect.right)) - innerRight) * 10) / 10),
      last: lastLine.length ? name(lastLine[lastLine.length - 1].element) : '',
      besideTitle: firstLine.length > 0 && firstLine[0].rect.top < title.bottom && firstLine[0].rect.bottom > title.top,
      overTitle: items.filter((item) => overlaps(item.rect)).map((item) => name(item.element)),
      outsideHeader: items
        .filter((item) => item.rect.left < innerLeft - 0.5 || item.rect.right > innerRight + 0.5)
        .map((item) => name(item.element)),
      truncated: [...group.querySelectorAll('*')]
        .filter((element) => element instanceof HTMLElement && element.clientWidth > 0 && element.scrollWidth > element.clientWidth)
        .map(name),
    };
  });
}

test.describe('Audit explorer header actions (W5d VRT, audit-ledger console)', () => {
  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      test(`the actions and the status chip stay one end-aligned group (${viewport.width}x${viewport.height}, ${theme})`, async ({ app, page }) => {
        await page.setViewportSize(viewport);
        await app.setTheme(theme);
        await app.gotoRoute('/audit-ledger');
        if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
        const header = page.locator(HEADER);
        await expect(header.getByRole('button', { name: 'Export PDF' })).toBeVisible();
        await expect(header.locator('.chip')).toHaveText(/^page 1 · \d+ rows$/);
        for (const consoleOpen of [false, true]) {
          await setConsole(page, consoleOpen);
          const where = `${viewport.width}x${viewport.height}, Console ${consoleOpen ? 'open' : 'closed'}`;
          const geometry = await headerGeometry(page);
          expect(geometry.lines, `${where}: one line of five items`).toEqual([
            ['Copy table context', 'Exclude my ledger reads', 'Page CSV', 'Export PDF', expect.stringMatching(/^page 1 · \d+ rows$/)],
          ]);
          expect(geometry.lineEnds, `${where}: the group ends at the header's inner right edge`).toEqual([
            expect.closeTo(0, 0),
          ]);
          expect(geometry.last, `${where}: the status chip closes the group`).toMatch(/^page 1 · \d+ rows$/);
          expect(geometry.overTitle, `${where}: nothing over the title block`).toEqual([]);
          expect(geometry.outsideHeader, `${where}: nothing past the header`).toEqual([]);
          expect(geometry.truncated, `${where}: nothing truncated`).toEqual([]);
          if (viewport.width === 1440) expect(geometry.besideTitle, `${where}: the group sits beside the title`).toBe(true);
        }
      });
    }
  }
});
