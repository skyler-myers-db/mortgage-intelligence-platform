/**
 * @vitest-environment happy-dom
 *
 * The floating panel's "New answer" jump (audit 2026-09-21 `genie-08` item 2,
 * w3-genie-reading review): an answer that lands while the reader is further
 * up raises it, and opening the panel follows the transcript again. The jump
 * raised before a close used to survive the reopen, offering a jump to where
 * the panel had already scrolled.
 */
import { act, useEffect, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useGenieTranscriptScroll, type GenieTranscriptScroll } from './useGenieTranscriptScroll';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let scroll: GenieTranscriptScroll | null = null;

function Transcript({ open, messages }: { open: boolean; messages: readonly string[] }) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const lastAnswerRef = useRef<HTMLDivElement>(null);
  const current = useGenieTranscriptScroll({ open, bodyRef, lastAnswerRef, messages, pendingQuestion: null, busy: false });
  // Hand the hook's handle to the test after the commit (never during render).
  useEffect(() => {
    scroll = current;
  });
  return (
    <div data-testid="body" ref={bodyRef}>
      {messages.map((text, index) => (
        <div key={text} ref={index === messages.length - 1 ? lastAnswerRef : undefined}>
          {text}
        </div>
      ))}
      {current.newAnswer && (
        <button type="button" className="genie__jump" onClick={current.jumpToNewAnswer}>
          New answer
        </button>
      )}
    </div>
  );
}

/** Give the transcript a scrolled layout: 1,000px of content in a 200px box. */
function layOut(body: HTMLElement, scrollTop: number) {
  Object.defineProperty(body, 'scrollHeight', { configurable: true, value: 1000 });
  Object.defineProperty(body, 'clientHeight', { configurable: true, value: 200 });
  body.scrollTop = scrollTop;
  body.getBoundingClientRect = () => ({ top: 0, bottom: 200, left: 0, right: 400, width: 400, height: 200, x: 0, y: 0, toJSON: () => ({}) });
}

describe('useGenieTranscriptScroll', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    scroll = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const body = () => container.querySelector<HTMLElement>('[data-testid="body"]')!;
  const jump = () => container.querySelector<HTMLButtonElement>('button.genie__jump');

  it('a "New answer" raised before a close is gone when the panel opens again', () => {
    act(() => root.render(<Transcript open messages={['first answer']} />));
    // The reader scrolls up to an earlier turn.
    layOut(body(), 0);
    act(() => {
      body().dispatchEvent(new Event('scroll'));
    });

    // An answer lands below the view: nothing moves, the jump is offered.
    act(() => scroll!.anchorNextAnswer());
    act(() => root.render(<Transcript open messages={['first answer', 'second answer']} />));
    const second = container.querySelectorAll<HTMLElement>('[data-testid="body"] > div')[1];
    second.getBoundingClientRect = () => ({ top: 800, bottom: 900, left: 0, right: 400, width: 400, height: 100, x: 0, y: 800, toJSON: () => ({}) });
    second.scrollIntoView = () => undefined;
    act(() => root.render(<Transcript open messages={['first answer', 'second answer']} />));
    expect(jump()?.textContent).toBe('New answer');

    act(() => root.render(<Transcript open={false} messages={['first answer', 'second answer']} />));
    act(() => root.render(<Transcript open messages={['first answer', 'second answer']} />));

    expect(jump()).toBeNull();
    // Opening followed the transcript to its end.
    expect(body().scrollTop).toBe(1000);
  });
});
