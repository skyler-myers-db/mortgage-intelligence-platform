/**
 * @vitest-environment happy-dom
 *
 * GenieRefusalReportConfirm rendered on its own (D-audit-reads-d): the
 * read-only question, the exact disclosure, the three prototype buttons in
 * order, focus on the first at reveal, and Esc on the shared escape stack
 * only while focus is inside the step (a non-modal layer declines
 * otherwise, so it never swallows an Esc meant for the page or the panel).
 * The card's posting, latch and focus return are pinned in
 * GenieRefusalCard.test.tsx; a chunk that fails to load in
 * GenieRefusalCard.confirmChunk.test.tsx.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GenieRefusalReportConfirm, REFUSAL_CAPTURE_DISCLOSURE } from './GenieRefusalReportConfirm';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const QUESTION = 'Which zyrplax borrowers are eligible for a HELOC?';

describe('GenieRefusalReportConfirm', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onReport = vi.fn();
  const onCancel = vi.fn();

  beforeEach(() => {
    onReport.mockReset();
    onCancel.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(
      <GenieRefusalReportConfirm id="refusal-confirm" question={QUESTION} onReport={onReport} onCancel={onCancel} />,
    ));
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const escape = () => act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  });

  it('renders the question read-only, the disclosure and the three prototype buttons', () => {
    const step = container.querySelector<HTMLElement>('.genie-answer__refusal-confirm');
    expect(step?.id).toBe('refusal-confirm');
    expect(step?.getAttribute('role')).toBe('group');
    const quote = step?.querySelector('blockquote.genie-answer__refusal-question');
    expect(quote?.textContent).toBe(QUESTION);
    expect(step?.querySelector('input, textarea, [contenteditable]')).toBeNull();
    expect(step?.querySelector('.genie-answer__refusal-disclosure')?.textContent).toBe(REFUSAL_CAPTURE_DISCLOSURE);
    expect(REFUSAL_CAPTURE_DISCLOSURE).toContain('questions that appear to name a person or a borrower are not kept');
    // Honest retention (owner re-ruling 2026-10-07): only the question ages out; the report and name stay.
    expect(REFUSAL_CAPTURE_DISCLOSURE).toContain('the report itself stays in the audit record');
    expect(REFUSAL_CAPTURE_DISCLOSURE).not.toMatch(/name (is|are) (kept|deleted|removed) for 90 days|and your name are kept/);
    const buttons = [...(step?.querySelectorAll('button') ?? [])].map((button) => [button.className, button.textContent]);
    expect(buttons).toEqual([
      ['btn btn--primary btn--sm', 'Report with my question'],
      ['btn btn--sm', 'Report without it'],
      ['btn btn--ghost btn--sm', 'Cancel'],
    ]);
  });

  it('moves focus to "Report with my question" on reveal', () => {
    expect(document.activeElement?.textContent).toBe('Report with my question');
  });

  it('reports with or without the question, and cancels', () => {
    const [withQuestion, without, cancel] = [...container.querySelectorAll<HTMLButtonElement>('button')];
    act(() => withQuestion.click());
    act(() => without.click());
    act(() => cancel.click());
    expect(onReport.mock.calls).toEqual([[true], [false]]);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('takes Esc while focus is inside the step and declines it otherwise', () => {
    escape();
    expect(onCancel).toHaveBeenCalledTimes(1);
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    act(() => outside.focus());
    escape();
    expect(onCancel).toHaveBeenCalledTimes(1);
    outside.remove();
  });

  it('leaves the escape stack when it unmounts', () => {
    act(() => root.render(<div />));
    escape();
    expect(onCancel).not.toHaveBeenCalled();
  });
});
