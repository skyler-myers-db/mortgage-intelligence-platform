/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const growthAgentCapabilities = vi.fn();

vi.mock('../../lib/api', () => ({
  api: {
    growthAgentCapabilities: (...args: unknown[]) => growthAgentCapabilities(...args),
  },
}));

import {
  GrowthAgentCapabilityPanel,
  PlatformCapabilitiesPanel,
} from './PlatformCapabilitiesPanel';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('GrowthAgentCapabilityPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('does not render an inconsistent non-claimable available capability as green or available', () => {
    act(() => {
      root.render(
        <GrowthAgentCapabilityPanel
          isPending={false}
          rows={[
            {
              key: 'agent_orchestrator',
              label: 'Agent Framework orchestration',
              ga: true,
              status: 'available',
              claimable: false,
              detail: 'inconsistent backend row',
            },
          ]}
        />,
      );
    });

    const chip = container.querySelector<HTMLElement>('.chip');
    expect(chip?.textContent).toContain('Configured');
    expect(chip?.classList.contains('chip--neutral')).toBe(true);
    expect(chip?.classList.contains('chip--success')).toBe(false);
  });

  it('does not render an inconsistent claimable non-available capability as green', () => {
    act(() => {
      root.render(
        <GrowthAgentCapabilityPanel
          isPending={false}
          rows={[
            {
              key: 'agent_orchestrator',
              label: 'Agent Framework orchestration',
              ga: true,
              status: 'not_provisioned',
              claimable: true,
              detail: 'inconsistent backend row',
            },
          ]}
        />,
      );
    });

    const chip = container.querySelector<HTMLElement>('.chip');
    expect(chip?.textContent).toContain('Not provisioned');
    expect(chip?.classList.contains('chip--neutral')).toBe(true);
    expect(chip?.classList.contains('chip--success')).toBe(false);
  });
});

describe('PlatformCapabilitiesPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  const CAPABILITIES = {
    workflows: [],
    monitors: [],
    capabilities: [
      {
        key: 'genie_conversation_api',
        label: 'Genie conversation planning',
        ga: true,
        status: 'available',
        claimable: true,
        detail: 'Live Genie conversation probe passed for this workspace.',
      },
    ],
  };

  function mount() {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <PlatformCapabilitiesPanel />
        </QueryClientProvider>,
      );
    });
  }

  async function waitUntil(cond: () => boolean, ms = 5_000) {
    const start = Date.now();
    while (!cond()) {
      if (Date.now() - start > ms) throw new Error('waitUntil timeout');
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }
  }

  beforeEach(() => {
    vi.resetAllMocks();
    growthAgentCapabilities.mockResolvedValue(CAPABILITIES);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('renders collapsed by default with the capability panel content hidden', () => {
    mount();

    // The summary is always visible so the surface stays discoverable.
    expect(container.textContent).toContain('Platform capabilities');
    expect(container.textContent).toContain('Live capability and proof status for this workspace');

    const toggle = container.querySelector<HTMLButtonElement>('button.appearance-toggle');
    expect(toggle).toBeTruthy();
    expect(toggle?.getAttribute('aria-expanded')).toBe('false');

    // Collapsed: the diagnostic panel body (and its honesty detail) is not rendered.
    expect(container.querySelector('.growth-agent-capability')).toBeNull();
    expect(container.textContent).not.toContain('Live Genie conversation probe passed');
  });

  it('expands on click to reveal live capability rows with honesty chips intact', async () => {
    mount();

    const toggle = container.querySelector<HTMLButtonElement>('button.appearance-toggle');
    act(() => {
      toggle?.click();
    });
    // Wait for the query to resolve into the expanded panel (the pending
    // "Checking" placeholder also carries .growth-agent-capability, so pin the
    // resolved row text instead).
    await waitUntil(() => container.textContent?.includes('Genie conversation planning') ?? false);

    expect(toggle?.getAttribute('aria-expanded')).toBe('true');
    expect(container.textContent).toContain('Live Genie conversation probe passed');
    // Honesty label intact: a claimable + available capability keeps its success chip.
    const chip = container.querySelector<HTMLElement>('.growth-agent-capability .chip');
    expect(chip?.classList.contains('chip--success')).toBe(true);
  });
});
