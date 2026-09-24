import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { AppShell } from '../components/layout/AppShell';
import { createMipQueryClient } from './queryClient';
import {
  GENIE_LAUNCHER_STATUS_ID,
  genieLauncherStateClass,
  genieLauncherStatusText,
  getGenieTurnStatus,
  setGenieTurnStatus,
  subscribeGenieTurnStatus,
} from './genieTurnStatus';

afterEach(() => {
  setGenieTurnStatus('idle');
});

describe('genieTurnStatus', () => {
  it('notifies subscribers on change only', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeGenieTurnStatus(listener);

    setGenieTurnStatus('running');
    setGenieTurnStatus('running');
    setGenieTurnStatus('ready');
    expect(listener).toHaveBeenCalledTimes(2);
    expect(getGenieTurnStatus()).toBe('ready');

    unsubscribe();
    setGenieTurnStatus('idle');
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it('maps each state to a launcher class and a screen-reader description', () => {
    expect(genieLauncherStateClass('idle')).toBe('');
    expect(genieLauncherStateClass('running')).toBe('is-genie-running');
    expect(genieLauncherStateClass('ready')).toBe('is-genie-ready');
    expect(genieLauncherStatusText('idle')).toBe('');
    expect(genieLauncherStatusText('running')).toContain('still working');
    expect(genieLauncherStatusText('ready')).toContain('answer ready');
  });

  it('says "answer ready" only for an answered turn: a withheld or failed one was only finished', () => {
    expect(genieLauncherStatusText('ready', 'answered')).toBe('Genie answer ready. Open Genie to read it.');
    for (const outcome of ['withheld', 'failed'] as const) {
      expect(genieLauncherStatusText('ready', outcome)).toBe('Genie finished your question. Open Genie to see the result.');
      expect(genieLauncherStatusText('running', outcome)).toBe('Genie is still working on your question.');
      expect(genieLauncherStatusText('idle', outcome)).toBe('');
    }
  });
});

/**
 * The desktop launcher. `.genie__fab` is `display: none` above 720px, so at
 * 1440x900 the topbar toggle is the ONLY place a closed panel can show that a
 * turn is still running or that its answer is waiting.
 */
describe('topbar Genie toggle reflects the turn behind a closed panel', () => {
  function toggleMarkup(): string {
    const html = renderToStaticMarkup(
      <QueryClientProvider client={createMipQueryClient()}>
        <MemoryRouter initialEntries={['/']}>
          <AppShell>
            <div>child</div>
          </AppShell>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const match = html.match(/<button[^>]*aria-label="Toggle Genie chat"[^>]*>/);
    if (!match) throw new Error('topbar Genie toggle not rendered');
    return match[0];
  }

  it('carries no status while idle', () => {
    const toggle = toggleMarkup();
    expect(toggle).not.toContain('is-genie-');
    expect(toggle).not.toContain('aria-describedby');
  });

  it('shows the running ring and points at the launcher description', () => {
    setGenieTurnStatus('running');
    const toggle = toggleMarkup();
    expect(toggle).toContain('is-genie-running');
    expect(toggle).toContain(`aria-describedby="${GENIE_LAUNCHER_STATUS_ID}"`);
  });

  it('shows the answer-ready badge', () => {
    setGenieTurnStatus('ready');
    const toggle = toggleMarkup();
    expect(toggle).toContain('is-genie-ready');
    expect(toggle).not.toContain('is-genie-running');
  });
});
