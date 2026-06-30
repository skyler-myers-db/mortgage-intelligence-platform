/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { GrowthAgentCapabilityPanel } from './ask-genie.growth-agent-capabilities';

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
