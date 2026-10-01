import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearActorScopedMemoryCaches } from './actorScopedMemoryCaches';
import { TOAST_LIMIT, clearToasts, dismissToast, getToasts, subscribeToasts, toast } from './toast';

afterEach(() => clearToasts());

describe('toast store (audit states-07)', () => {
  it('raises a toast with its tone, detail and audit event', () => {
    const id = toast.success('Build saved', { detail: 'IL refi cohort', auditEventId: 'evt-1' });
    expect(getToasts()).toEqual([
      {
        id,
        tone: 'success',
        title: 'Build saved',
        detail: 'IL refi cohort',
        auditEventId: 'evt-1',
        count: 1,
        revision: 0,
        action: null,
        actionReadyAt: null,
      },
    ]);
  });

  it('coalesces an identical toast into a count instead of stacking a copy', () => {
    const first = toast.error('Copy failed');
    const second = toast.error('Copy failed');
    expect(second).toBe(first);
    expect(getToasts()).toHaveLength(1);
    expect(getToasts()[0]).toMatchObject({ count: 2, revision: 1 });
  });

  it('keeps toasts for different audit events apart: each links its own row', () => {
    toast.success('Build saved', { auditEventId: 'evt-1' });
    toast.success('Build saved', { auditEventId: 'evt-2' });
    expect(getToasts().map((item) => item.auditEventId)).toEqual(['evt-1', 'evt-2']);
  });

  it(`caps the region at ${TOAST_LIMIT}, evicting the oldest success before any failure`, () => {
    toast.error('Save failed');
    toast.success('One');
    toast.success('Two');
    toast.success('Three');
    expect(getToasts().map((item) => item.title)).toEqual(['Save failed', 'Two', 'Three']);
    toast.error('Copy failed');
    expect(getToasts().map((item) => item.title)).toEqual(['Save failed', 'Three', 'Copy failed']);
  });

  it('evicts the oldest failure only when nothing else is left to drop', () => {
    toast.error('A');
    toast.error('B');
    toast.error('C');
    toast.error('D');
    expect(getToasts().map((item) => item.title)).toEqual(['B', 'C', 'D']);
  });

  it('dismisses by id and notifies subscribers once per change', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToasts(listener);
    const id = toast.success('Build link copied');
    dismissToast(id);
    dismissToast(id);
    expect(getToasts()).toEqual([]);
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    toast.success('After unsubscribe');
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it('raises an info toast (the shell platform, deviation:toast-actions-and-path)', () => {
    const id = toast.info('The signed-in user changed, so this tab was reset.');
    expect(getToasts()).toEqual([expect.objectContaining({ id, tone: 'info', action: null, actionReadyAt: null })]);
  });

  it('never coalesces a toast with an action, nor coalesces into one', () => {
    const retry = vi.fn();
    const first = toast.error('Approve failed', { action: { label: 'Retry', onAction: retry } });
    const second = toast.error('Approve failed', { action: { label: 'Retry', onAction: retry } });
    expect(second).not.toBe(first);
    const plain = toast.error('Approve failed');
    expect(getToasts().map((item) => item.id)).toEqual([first, second, plain]);
    expect(getToasts().every((item) => item.count === 1)).toBe(true);
  });

  it('holds an action until a positive retryAfterMs has passed; ignores retryAfterMs without an action', () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-10-01T12:00:00Z'));
      const action = { label: 'Retry', onAction: () => undefined };
      toast.error('Rate limited', { action, retryAfterMs: 12_000 });
      toast.error('Rate limited again', { action, retryAfterMs: 0 });
      toast.error('No action', { retryAfterMs: 12_000 });
      expect(getToasts().map((item) => item.actionReadyAt)).toEqual([Date.now() + 12_000, null, null]);
    } finally {
      vi.useRealTimers();
    }
  });

  it('evicts the oldest success, then the oldest info, then the oldest failure', () => {
    toast.error('Failed');
    toast.info('Notice');
    toast.success('Saved');
    toast.error('Failed again');
    expect(getToasts().map((item) => item.title), 'the success goes first').toEqual(['Failed', 'Notice', 'Failed again']);
    toast.error('Third failure');
    expect(getToasts().map((item) => item.title), 'then the info').toEqual(['Failed', 'Failed again', 'Third failure']);
  });

  it('drops every toast when the signed-in actor changes', () => {
    // AppShell's actor-change reset calls clearActorScopedMemoryCaches; one
    // operator's build names, routing and audit ids never reach the next.
    toast.error('Save failed', { detail: 'Summit IL refi cohort' });
    toast.success('Approval routed', { detail: 'Assigned to lo.alpha@summit.example', auditEventId: 'evt-1' });
    clearActorScopedMemoryCaches();
    expect(getToasts()).toEqual([]);
  });
});
