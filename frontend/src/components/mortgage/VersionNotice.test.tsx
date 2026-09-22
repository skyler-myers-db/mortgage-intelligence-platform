// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HealthPayload } from '../../lib/apiTypes';
import { designCss } from '../../test/designCss';
import { HealthProvider } from '../HealthProvider';
import { VersionNotice } from './VersionNotice';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * "A new version is available" (audit 2026-09-21 `bundle-01`, last part).
 *
 * The health body already carried `git_sha`; nothing read it. Proven at the
 * provider + rendered notice: a scripted poll reports one sha per probe and the
 * test asserts what the user sees.
 */
describe('VersionNotice', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    Reflect.deleteProperty(document, 'visibilityState');
    vi.useRealTimers();
  });

  /** One health probe per entry; the last entry repeats. `undefined` = a body with no sha. */
  async function mountWithShas(shas: Array<string | null | undefined>, onReload = vi.fn()) {
    let probe = 0;
    const fetchHealth = vi.fn(async (): Promise<HealthPayload> => {
      const sha = shas[Math.min(probe, shas.length - 1)];
      probe += 1;
      return {
        status: 'ok',
        mode: 'live',
        dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
        ...(sha === undefined ? {} : { git_sha: sha }),
      };
    });
    await act(async () => {
      root.render(
        <HealthProvider pollIntervalOkMs={8000} fetchHealth={fetchHealth}>
          <VersionNotice onReload={onReload} />
        </HealthProvider>,
      );
    });
    await nextPoll(0);
    return { fetchHealth, onReload };
  }

  async function nextPoll(ms = 8000): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  const notice = () => container.querySelector('.degraded-banner--info');

  it('stays hidden while every poll reports the build the tab loaded', async () => {
    const { fetchHealth } = await mountWithShas(['abc1234']);
    await nextPoll();
    await nextPoll();

    expect(fetchHealth).toHaveBeenCalledTimes(3);
    expect(notice()).toBeNull();
  });

  it('appears when a later poll reports a different build, and offers Reload', async () => {
    const { onReload } = await mountWithShas(['abc1234', 'abc1234', 'def5678']);
    await nextPoll();
    expect(notice()).toBeNull();

    await nextPoll();

    expect(notice()?.textContent).toContain('A new version is available');
    expect(notice()?.getAttribute('role')).toBe('status');
    const reload = notice()?.querySelector('button') as HTMLButtonElement;
    expect(reload.textContent).toBe('Reload');
    act(() => reload.click());
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it('stays up once shown, even if a stale replica answers the next poll', async () => {
    await mountWithShas(['abc1234', 'def5678', 'abc1234']);
    await nextPoll();
    expect(notice()).not.toBeNull();

    await nextPoll();

    expect(notice()).not.toBeNull();
  });

  it('ignores missing, null and empty shas (bare deploys report none)', async () => {
    await mountWithShas([undefined, 'abc1234', '', null, '   ', 'abc1234']);
    for (let i = 0; i < 5; i += 1) await nextPoll();

    expect(notice()).toBeNull();
  });

  it('a sha-less first poll does not become the baseline', async () => {
    await mountWithShas([undefined, 'abc1234', 'def5678']);
    await nextPoll();
    expect(notice()).toBeNull();

    await nextPoll();

    expect(notice()).not.toBeNull();
  });

  it('is an info variant of the banner block, drawn from tokens', () => {
    const css = designCss();
    expect(css).toMatch(
      /\.degraded-banner--info\s*\{\s*background:\s*var\(--accent-soft\);\s*border-color:\s*var\(--chip-line\);/,
    );
    expect(css).toMatch(/\.degraded-banner__actions\s*\{[^}]*gap:\s*var\(--sp-2\)/);
  });

  it('renders nothing outside a HealthProvider', async () => {
    await act(async () => {
      root.render(<VersionNotice />);
    });
    expect(container.innerHTML).toBe('');
  });
});
