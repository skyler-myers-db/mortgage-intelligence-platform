import { describe, expect, it, vi } from 'vitest';
import { QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import {
  AppShell,
  applyActorScopedStateTransition,
  shouldResetActorScopedStateForActorChange,
} from './AppShell';
import { systemStatusViewModel } from './Topbar';
import { createMipQueryClient } from '../../lib/queryClient';

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
  const queryClient = createMipQueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <AppShell>
          <div data-testid="child">hello</div>
        </AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
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

describe('actor-scoped state reset guard', () => {
  it('does not clear on the first trusted actor key', () => {
    expect(shouldResetActorScopedStateForActorChange(null, 'actor_a')).toBe(false);
  });

  it('clears when Databricks swaps the trusted actor key', () => {
    expect(shouldResetActorScopedStateForActorChange('actor_a', 'actor_b')).toBe(true);
  });

  it('clears when the trusted actor key disappears after being present', () => {
    expect(shouldResetActorScopedStateForActorChange('actor_a', null)).toBe(true);
  });

  it('clears QueryClient, AppContext, and Genie state on actor-boundary changes', () => {
    const clearQueryClient = vi.fn();
    const clearAppState = vi.fn();
    const clearBrowserState = vi.fn();
    const clearMemoryState = vi.fn();

    const next = applyActorScopedStateTransition({
      previousActorCacheKey: 'actor_a',
      nextActorCacheKey: 'actor_b',
      clearQueryClient,
      clearAppState,
      clearBrowserState,
      clearMemoryState,
    });

    expect(next).toBe('actor_b');
    expect(clearQueryClient).toHaveBeenCalledTimes(1);
    expect(clearAppState).toHaveBeenCalledTimes(1);
    expect(clearBrowserState).toHaveBeenCalledTimes(1);
    expect(clearMemoryState).toHaveBeenCalledTimes(1);
  });

  it('does not clear scoped state for the initial actor observation', () => {
    const clearQueryClient = vi.fn();
    const clearAppState = vi.fn();
    const clearBrowserState = vi.fn();
    const clearMemoryState = vi.fn();

    const next = applyActorScopedStateTransition({
      previousActorCacheKey: null,
      nextActorCacheKey: 'actor_a',
      clearQueryClient,
      clearAppState,
      clearBrowserState,
      clearMemoryState,
    });

    expect(next).toBe('actor_a');
    expect(clearQueryClient).not.toHaveBeenCalled();
    expect(clearAppState).not.toHaveBeenCalled();
    expect(clearMemoryState).not.toHaveBeenCalled();
    expect(clearBrowserState).not.toHaveBeenCalled();
  });
});

describe('system status view model', () => {
  it('does not show stale environment or unknown breaker copy when diagnostics are hidden', () => {
    const status = systemStatusViewModel({
      status: 'ok',
      mode: 'live',
      dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
    });

    expect(status.label).toBe('Live');
    expect(status.ariaLabel).toBe('System status: Live.');
    expect(status.tooltip).not.toContain('env=');
    expect(status.tooltip).not.toContain('unknown');
    expect(status.tooltip).not.toContain('loading');
  });
});
