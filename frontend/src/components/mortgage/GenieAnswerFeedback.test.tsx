/**
 * @vitest-environment happy-dom
 *
 * GenieAnswerFeedback — thumbs vote + optional comment posting to
 * /api/genie/feedback. Covers: correct request body, disable-after-success,
 * 422 detail surfaced WITHOUT echoing the rejected comment, generic error on
 * 415/5xx, the async double-submit latch, and the no-render guard when the
 * payload lacks a conversation/message id.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { genieFeedback } = vi.hoisted(() => ({ genieFeedback: vi.fn() }));
vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, api: { genieFeedback } };
});

import { GenieAnswerFeedback } from './GenieAnswerFeedback';
import { ApiError } from '../../lib/api';

function upBtn(container: HTMLElement) {
  return container.querySelector<HTMLButtonElement>('[data-testid="genie-feedback-up"]');
}
function downBtn(container: HTMLElement) {
  return container.querySelector<HTMLButtonElement>('[data-testid="genie-feedback-down"]');
}

// React tracks the controlled value via a native setter; a plain `.value =`
// in happy-dom bypasses it, so route through the prototype setter before
// firing the input event (same pattern as CommandPalette.test / ask-genie).
function typeInComment(container: HTMLElement, value: string) {
  const textarea = container.querySelector<HTMLTextAreaElement>('.genie-feedback__comment')!;
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
  setter.call(textarea, value);
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('GenieAnswerFeedback', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    genieFeedback.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('renders nothing without a conversation id or message id', () => {
    act(() => root.render(<GenieAnswerFeedback conversationId={null} messageId="m1" />));
    expect(upBtn(container)).toBeNull();
    act(() => root.render(<GenieAnswerFeedback conversationId="c1" messageId={null} />));
    expect(upBtn(container)).toBeNull();
  });

  it('posts the correct body and locks to the recorded state after success', async () => {
    genieFeedback.mockResolvedValue({ accepted: true, audit_event_id: 'evt-1' });
    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />);
    });

    // Type a comment, then vote up.
    await act(async () => {
      typeInComment(container, 'clear and fast');
    });
    await act(async () => {
      upBtn(container)!.click();
      await Promise.resolve();
    });

    expect(genieFeedback).toHaveBeenCalledTimes(1);
    expect(genieFeedback).toHaveBeenCalledWith({
      conversation_id: 'c1',
      message_id: 'm1',
      helpful: true,
      comment: 'clear and fast',
    });
    // Recorded state: vote buttons gone, subtle confirmation shown.
    expect(upBtn(container)).toBeNull();
    expect(container.querySelector('.genie-feedback--done')).not.toBeNull();
    expect(container.textContent).toContain('Feedback recorded');
  });

  it('omits the comment field entirely when the user did not type one', async () => {
    genieFeedback.mockResolvedValue({ accepted: true });
    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />);
    });
    await act(async () => {
      downBtn(container)!.click();
      await Promise.resolve();
    });
    expect(genieFeedback).toHaveBeenCalledWith({
      conversation_id: 'c1',
      message_id: 'm1',
      helpful: false,
    });
  });

  it('shows the 422 detail inline and never echoes the rejected comment text', async () => {
    genieFeedback.mockRejectedValue(
      new ApiError('Comment appears to contain personal data.', {
        path: '/api/genie/feedback',
        status: 422,
      }),
    );
    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />);
    });
    await act(async () => {
      typeInComment(container, 'call jane@example.com');
    });
    await act(async () => {
      upBtn(container)!.click();
      await Promise.resolve();
    });

    const err = container.querySelector('.genie-feedback__error');
    expect(err).not.toBeNull();
    expect(err!.textContent).toContain('Comment appears to contain personal data.');
    // The rejected comment must NOT be reflected anywhere in the error UI.
    expect(err!.textContent).not.toContain('jane@example.com');
    // Not recorded — the vote buttons stay so the user can revise + retry.
    expect(upBtn(container)).not.toBeNull();
  });

  it('shows a generic error on a 415 wrong-content-type response', async () => {
    genieFeedback.mockRejectedValue(
      new ApiError('Unsupported Media Type', { path: '/api/genie/feedback', status: 415 }),
    );
    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />);
    });
    await act(async () => {
      upBtn(container)!.click();
      await Promise.resolve();
    });
    const err = container.querySelector('.genie-feedback__error');
    expect(err).not.toBeNull();
    expect(err!.textContent).toContain('Feedback could not be recorded');
    // The raw 415 status text is not surfaced to the user.
    expect(err!.textContent).not.toContain('Unsupported Media Type');
  });

  it('latches against a double submit while a request is in flight', async () => {
    let resolveFn: ((v: unknown) => void) | null = null;
    genieFeedback.mockImplementation(
      () => new Promise((resolve) => {
        resolveFn = resolve;
      }),
    );
    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />);
    });
    // Fire two votes back-to-back before the first resolves.
    await act(async () => {
      upBtn(container)!.click();
      downBtn(container)?.click();
      await Promise.resolve();
    });
    expect(genieFeedback).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveFn?.({ accepted: true });
      await Promise.resolve();
    });
    expect(container.querySelector('.genie-feedback--done')).not.toBeNull();
  });
});
