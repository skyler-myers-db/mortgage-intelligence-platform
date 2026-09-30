/**
 * @vitest-environment happy-dom
 *
 * The shared mount() helper (audit quality-06): it flushes effects inside
 * act, re-renders, unmounts, cleans up after each test on its own, and says
 * clearly when a test forgot its DOM environment.
 */
import { useEffect, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount } from './render';

function EffectProbe({ label }: { label: string }) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setReady(true);
  }, []);
  return <p data-testid="probe">{ready ? `ready: ${label}` : 'pending'}</p>;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('mount()', () => {
  it('flushes an effect before it resolves', async () => {
    const { container } = await mount(<EffectProbe label="first" />);
    expect(container.textContent).toBe('ready: first');
    expect(container.parentElement).toBe(document.body);
  });

  it('starts every test with an empty body: the previous test left its root mounted', () => {
    // The test above never unmounted; mount()'s afterEach did.
    expect(document.body.children).toHaveLength(0);
  });

  it('rerender updates the same root in place', async () => {
    const view = await mount(<EffectProbe label="first" />);
    const node = view.container.querySelector('[data-testid="probe"]');
    await view.rerender(<EffectProbe label="second" />);
    expect(view.container.textContent).toBe('ready: second');
    expect(view.container.querySelector('[data-testid="probe"]')).toBe(node);
  });

  it('unmount empties the root and removes the container it created', async () => {
    const view = await mount(<EffectProbe label="gone" />);
    view.unmount();
    expect(view.container.isConnected).toBe(false);
    expect(view.container.childNodes).toHaveLength(0);
    // A second unmount (or the afterEach) is a no-op.
    expect(() => view.unmount()).not.toThrow();
  });

  it('renders into a container the caller owns, and leaves it in place', async () => {
    const host = document.body.appendChild(document.createElement('section'));
    const view = await mount(<EffectProbe label="hosted" />, { container: host });
    expect(view.container).toBe(host);
    expect(host.textContent).toBe('ready: hosted');
    view.unmount();
    expect(host.isConnected).toBe(true);
    expect(host.childNodes).toHaveLength(0);
    host.remove();
  });

  it('fails with a clear message in a node-environment test', async () => {
    vi.stubGlobal('document', undefined);
    await expect(mount(<EffectProbe label="nowhere" />)).rejects.toThrow(/needs a DOM.*@vitest-environment happy-dom/);
  });
});
