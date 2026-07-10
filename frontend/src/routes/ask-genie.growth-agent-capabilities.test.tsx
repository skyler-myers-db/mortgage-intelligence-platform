/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  GrowthAgentCapabilityDisclosure,
  GrowthAgentCapabilityPanel,
} from './ask-genie.growth-agent-capabilities';

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

describe('GrowthAgentCapabilityDisclosure', () => {
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

  it('renders collapsed by default with the capability panel content hidden', () => {
    act(() => {
      root.render(
        <GrowthAgentCapabilityDisclosure
          isPending={false}
          rows={[
            {
              key: 'genie_conversation',
              label: 'Genie conversation planning',
              ga: true,
              status: 'available',
              claimable: true,
              detail: 'Live Genie conversation probe passed for this workspace.',
            },
          ]}
        />,
      );
    });

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

  it('expands on click to reveal the capability panel content and honesty chip unchanged', () => {
    act(() => {
      root.render(
        <GrowthAgentCapabilityDisclosure
          isPending={false}
          rows={[
            {
              key: 'genie_conversation',
              label: 'Genie conversation planning',
              ga: true,
              status: 'available',
              claimable: true,
              detail: 'Live Genie conversation probe passed for this workspace.',
            },
          ]}
        />,
      );
    });

    const toggle = container.querySelector<HTMLButtonElement>('button.appearance-toggle');
    act(() => {
      toggle?.click();
    });

    expect(toggle?.getAttribute('aria-expanded')).toBe('true');
    expect(container.querySelector('.growth-agent-capability')).toBeTruthy();
    expect(container.textContent).toContain('Genie conversation planning');
    expect(container.textContent).toContain('Live Genie conversation probe passed');
    // Honesty label intact once open: a claimable + available capability keeps its success chip.
    const chip = container.querySelector<HTMLElement>('.growth-agent-capability .chip');
    expect(chip?.classList.contains('chip--success')).toBe(true);
  });
});
