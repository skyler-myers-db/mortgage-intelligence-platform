/**
 * @vitest-environment happy-dom
 *
 * GenieAnswerFeedback — replay-safe thumbs-only feedback. Covers the request
 * contract, retry idempotency, disable-after-success, generic error handling,
 * the async double-submit latch, and missing conversation/message guards.
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

  it('uses thumb icons and does not collect free text', () => {
    act(() => root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />));

    expect(upBtn(container)?.querySelector('path[d="M7 10v12"]')).not.toBeNull();
    expect(downBtn(container)?.querySelector('path[d="M17 14V2"]')).not.toBeNull();
    expect(container.querySelector('textarea')).toBeNull();
  });

  it('posts the correct body and locks to the recorded state after success', async () => {
    genieFeedback.mockResolvedValue({ accepted: true, audit_event_id: 'evt-1' });
    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />);
    });

    await act(async () => {
      upBtn(container)!.click();
      await Promise.resolve();
    });

    expect(genieFeedback).toHaveBeenCalledTimes(1);
    expect(genieFeedback).toHaveBeenCalledWith(expect.objectContaining({
      conversation_id: 'c1',
      message_id: 'm1',
      helpful: true,
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    }));
    expect(genieFeedback.mock.calls[0][0]).not.toHaveProperty('comment');
    // Recorded state: vote buttons gone, subtle confirmation shown.
    expect(upBtn(container)).toBeNull();
    expect(container.querySelector('.genie-feedback--done')).not.toBeNull();
    expect(container.textContent).toContain('Feedback recorded');
  });

  it('always omits the comment field', async () => {
    genieFeedback.mockResolvedValue({ accepted: true });
    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />);
    });
    await act(async () => {
      downBtn(container)!.click();
      await Promise.resolve();
    });
    expect(genieFeedback).toHaveBeenCalledWith(expect.objectContaining({
      conversation_id: 'c1',
      message_id: 'm1',
      helpful: false,
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    }));
    expect(genieFeedback.mock.calls[0][0]).not.toHaveProperty('comment');
  });

  it('reuses the same request id when a failed vote is retried', async () => {
    genieFeedback
      .mockRejectedValueOnce(new ApiError('Invalid feedback request.', {
        path: '/api/genie/feedback', status: 422,
      }))
      .mockResolvedValueOnce({ accepted: true, audit_event_id: 'evt-1' });
    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />);
    });
    await act(async () => {
      upBtn(container)!.click();
      await Promise.resolve();
    });
    const firstRequestId = genieFeedback.mock.calls[0][0].request_id;
    expect(container.querySelector('.genie-feedback__error')?.textContent).toContain(
      'Invalid feedback request.',
    );
    await act(async () => {
      upBtn(container)!.click();
      await Promise.resolve();
    });
    expect(genieFeedback.mock.calls[1][0].request_id).toBe(firstRequestId);
    expect(container.querySelector('.genie-feedback--done')).not.toBeNull();
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

  it('preserves the request id when a 409 may mean the vote is still in progress', async () => {
    genieFeedback
      .mockRejectedValueOnce(new ApiError('conflict', { path: '/api/genie/feedback', status: 409 }))
      .mockResolvedValueOnce({ accepted: true, audit_event_id: 'evt-2' });
    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />);
    });
    await act(async () => {
      upBtn(container)!.click();
      await Promise.resolve();
    });
    const firstRequestId = genieFeedback.mock.calls[0][0].request_id;
    expect(container.textContent).toContain('still being processed');
    await act(async () => {
      upBtn(container)!.click();
      await Promise.resolve();
    });
    expect(genieFeedback.mock.calls[1][0].request_id).toBe(firstRequestId);
    expect(container.querySelector('.genie-feedback--done')).not.toBeNull();
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

  it('resets recorded state and request ids when the answer identity changes', async () => {
    genieFeedback.mockResolvedValue({ accepted: true, audit_event_id: 'evt-1' });
    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />);
    });
    await act(async () => {
      upBtn(container)!.click();
      await Promise.resolve();
    });
    const firstRequestId = genieFeedback.mock.calls[0][0].request_id;
    expect(container.querySelector('.genie-feedback--done')).not.toBeNull();

    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c2" messageId="m2" />);
      await Promise.resolve();
    });
    expect(container.querySelector('.genie-feedback--done')).toBeNull();
    expect(upBtn(container)).not.toBeNull();

    await act(async () => {
      downBtn(container)!.click();
      await Promise.resolve();
    });
    expect(genieFeedback).toHaveBeenCalledTimes(2);
    expect(genieFeedback.mock.calls[1][0]).toEqual(expect.objectContaining({
      conversation_id: 'c2',
      message_id: 'm2',
      helpful: false,
    }));
    expect(genieFeedback.mock.calls[1][0].request_id).not.toBe(firstRequestId);
  });

  it('ignores completion from a vote submitted for an earlier answer', async () => {
    let resolveFirst: ((value: unknown) => void) | null = null;
    genieFeedback.mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveFirst = resolve;
      }),
    );
    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c1" messageId="m1" />);
    });
    await act(async () => {
      upBtn(container)!.click();
      await Promise.resolve();
    });

    await act(async () => {
      root.render(<GenieAnswerFeedback conversationId="c2" messageId="m2" />);
      await Promise.resolve();
    });
    await act(async () => {
      resolveFirst?.({ accepted: true });
      await Promise.resolve();
    });

    expect(container.querySelector('.genie-feedback--done')).toBeNull();
    expect(upBtn(container)).not.toBeNull();
  });
});
