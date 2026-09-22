/**
 * retryUntil — run `attempt` now and, if it is not satisfied yet, again after
 * each DOM change under `root`, until it succeeds or `deadlineMs` passes.
 *
 * Routes here are lazy and their data is asynchronous, so at the moment the
 * location changes the thing a navigation effect needs (content tall enough to
 * restore a scroll offset, a `#hash` target, the page `<h1>`) is often not in
 * the DOM yet. A MutationObserver wakes the attempt exactly when React commits
 * more content, instead of polling layout on every frame. The observer is
 * bounded: it disconnects on success, on `cancel()`, or at the deadline, when
 * `onExpire` gets one last chance to apply a best-effort fallback.
 *
 * Returns `cancel`. Callers cancel on the next navigation and on unmount.
 */
export function retryUntil(
  attempt: () => boolean,
  root: Node,
  deadlineMs: number,
  onExpire?: () => void,
): () => void {
  if (attempt()) return () => undefined;
  if (typeof MutationObserver === 'undefined') {
    onExpire?.();
    return () => undefined;
  }

  let settled = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const observer = new MutationObserver(() => {
    if (!settled && attempt()) finish();
  });

  function finish(): void {
    settled = true;
    observer.disconnect();
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  timer = setTimeout(() => {
    if (settled) return;
    finish();
    onExpire?.();
  }, deadlineMs);
  observer.observe(root, { childList: true, subtree: true });
  return finish;
}
