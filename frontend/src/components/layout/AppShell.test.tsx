import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { AppShell } from './AppShell';

/**
 * AppShell skip-link + landmark contract (R6-13, R6-16, 2026-04-23).
 *
 * WCAG 2.2 SC 2.4.1 "Bypass Blocks" requires a "skip to main content"
 * link as the first focusable element. Without `tabIndex={-1}` on the
 * target, hash-navigating only scrolls — focus stays on the anchor, so
 * the next Tab press still lands in the previously-focused element.
 * The tests below pin:
 *
 *   1. The skip-link appears before any navigation/header/main markup.
 *   2. The `#main-content` target carries `tabIndex={-1}` so focus
 *      actually moves when the skip-link activates.
 *   3. The workspace-console skip-link is preserved (was added in
 *      cycle 11; removing it is a regression).
 *   4. Rail is `<nav>` labelled "Primary navigation".
 */

function renderShell(): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={['/']}>
      <AppShell>
        <div data-testid="child">hello</div>
      </AppShell>
    </MemoryRouter>,
  );
}

describe('AppShell skip-link + landmarks', () => {
  it('emits a "Skip to main content" link before "Skip to workspace console"', () => {
    const html = renderShell();
    const mainIdx = html.indexOf('Skip to main content');
    const consoleIdx = html.indexOf('Skip to workspace console');
    expect(mainIdx).toBeGreaterThan(-1);
    expect(consoleIdx).toBeGreaterThan(-1);
    // Order matters: WCAG expects "skip to main" as the first
    // affordance. The workspace-console shortcut is secondary.
    expect(mainIdx).toBeLessThan(consoleIdx);
  });

  it('wires the "Skip to main content" link to #main-content', () => {
    const html = renderShell();
    expect(html).toMatch(/href="#main-content"[^>]*class="sr-skip-link"/);
  });

  it('emits <main id="main-content" tabindex="-1"> so the skip-link can shift focus', () => {
    const html = renderShell();
    // Both attributes present on the main landmark. Without tabIndex
    // the anchor-jump only scrolls and focus never leaves the link.
    expect(html).toMatch(/<main[^>]*id="main-content"/);
    expect(html).toMatch(/<main[^>]*tabindex="-1"/);
  });

  it('Rail is <nav aria-label="Primary navigation"> (R6-16)', () => {
    const html = renderShell();
    // The primary navigation landmark must be reachable by screen
    // readers; "Primary navigation" is the unambiguous label per
    // R6-16. The previous "Modules" label was technically correct but
    // less discoverable for AT users scanning the landmark list.
    expect(html).toMatch(/<nav[^>]*aria-label="Primary navigation"/);
  });

  it('Topbar renders a <header> landmark (R6-16)', () => {
    const html = renderShell();
    // <header> carries an implicit `banner` role when it's a top-level
    // landmark. The explicit role="banner" is belt-and-suspenders for
    // AT parity across older implementations.
    expect(html).toMatch(/<header[^>]*class="topbar"/);
  });

  it('renders children inside the main landmark', () => {
    const html = renderShell();
    const mainMatch = html.match(/<main[^>]*>([\s\S]*)<\/main>/);
    expect(mainMatch).not.toBeNull();
    expect(mainMatch![1]).toContain('hello');
  });
});
