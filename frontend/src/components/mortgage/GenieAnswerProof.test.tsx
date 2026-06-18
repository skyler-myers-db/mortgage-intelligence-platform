/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { GenieProofPanel } from './GenieAnswerProof';

function payload(elapsedMs: number | null): GenieAnswerShape {
  return {
    answer: 'Trusted SQL answer.',
    source: 'trusted_sql',
    trusted_assets: [],
    proof: {
      trusted: true,
      row_count: 1,
      source_assets: [],
      elapsed_ms: elapsedMs,
    },
  } as GenieAnswerShape;
}

describe('GenieProofPanel', () => {
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

  it('renders zero-millisecond proof latency as an observed value', () => {
    act(() => {
      root.render(<GenieProofPanel payload={payload(0)} onOpenSource={() => {}} />);
    });

    expect(container.textContent).toContain('Latency');
    expect(container.textContent).toContain('0 ms');
  });

  it('uses a dash only when proof latency is absent', () => {
    act(() => {
      root.render(<GenieProofPanel payload={payload(null)} onOpenSource={() => {}} />);
    });

    expect(container.textContent).toContain('Latency');
    expect(container.textContent).toContain('—');
  });

  it('does not render a source-assets section when proof has no source assets', () => {
    act(() => {
      root.render(<GenieProofPanel payload={payload(12)} onOpenSource={() => {}} />);
    });

    expect(container.textContent).not.toContain('Source UC assets');
  });

  it('does not render query trace content on untrusted proofs', () => {
    const blocked = payload(12);
    blocked.source = 'policy_blocked';
    blocked.proof = {
      ...(blocked.proof ?? {}),
      trusted: false,
      reasoning_trace: [
        {
          kind: 'THOUGHT_TYPE_TEXT',
          content: 'Unsafe trace mentioned jane@example.com and 123 Main St.',
        },
      ],
    };

    act(() => {
      root.render(<GenieProofPanel payload={blocked} onOpenSource={() => {}} />);
    });

    expect(container.textContent).toContain('Review required');
    expect(container.textContent).not.toContain('Unsafe trace');
    expect(container.textContent).not.toContain('jane@example.com');
    expect(container.textContent).not.toContain('123 Main St.');
  });
});
