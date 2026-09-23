import { afterEach, describe, expect, it, vi } from 'vitest';
import { TOAST_LIMIT, clearToasts, dismissToast, getToasts, subscribeToasts, toast } from './toast';

afterEach(() => clearToasts());

describe('toast store (audit states-07)', () => {
  it('raises a toast with its tone, detail and audit event', () => {
    const id = toast.success('Build saved', { detail: 'IL refi cohort', auditEventId: 'evt-1' });
    expect(getToasts()).toEqual([
      { id, tone: 'success', title: 'Build saved', detail: 'IL refi cohort', auditEventId: 'evt-1', count: 1, revision: 0 },
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
});
