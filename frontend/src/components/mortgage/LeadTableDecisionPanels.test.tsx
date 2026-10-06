/**
 * @vitest-environment happy-dom
 *
 * The Lead Queue's inline decision forms own their fields (audit runtime-04
 * slice 2). The reject form submits its own reason and rationale; the
 * disposition form opens on the given loan officer, says its pre-flight
 * validation inside the form as an alert (it used to be the table's alert,
 * far from the field) and submits exactly the payload the POST sends.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SalesTeamMember } from '../../types';
import { outreachMutationKeys } from '../../lib/mutations/outreach';
import { LeadDispositionPanel, LeadRejectPanel } from './LeadTableDecisionPanels';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const LO_A = 'lo.alpha@summit.example';
const TEAM: SalesTeamMember[] = [
  { email: LO_A, display_label: 'Loan Officer A', role: 'loan_officer', region: 'Midwest', manager_email: null, capacity_per_day: 25, active: true },
];
const BORROWER = 'B-PANELS0000001';

function setValue(target: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string): void {
  const prototype = target instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : target instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  act(() => {
    Object.getOwnPropertyDescriptor(prototype, 'value')?.set?.call(target, value);
    target.dispatchEvent(new Event(target instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}

describe('Lead Queue decision forms own their fields', () => {
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

  const submit = () => act(() => {
    container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });

  it('the reject form submits its own reason and rationale', () => {
    const onSubmit = vi.fn();
    act(() => root.render(<LeadRejectPanel borrowerId={BORROWER} onCancel={vi.fn()} onSubmit={onSubmit} />));
    setValue(container.querySelector('select')!, 'data_quality');
    setValue(container.querySelector('textarea')!, ' Stale lien record. ');
    submit();
    expect(onSubmit).toHaveBeenCalledWith('data_quality', ' Stale lien record. ');
  });

  it('the reject form has no default reason: a submit without one focuses Reason and sends nothing', () => {
    const onSubmit = vi.fn();
    act(() => root.render(<LeadRejectPanel borrowerId={BORROWER} onCancel={vi.fn()} onSubmit={onSubmit} />));
    const select = container.querySelector('select')!;
    const confirm = container.querySelector<HTMLButtonElement>('[data-testid="lead-reject-confirm"]')!;
    expect(select.value).toBe('');
    expect(select.options[0].textContent).toBe('Choose a reason');
    expect(confirm.getAttribute('aria-disabled')).toBe('true');
    expect(confirm.disabled, 'aria-disabled, never native disabled').toBe(false);

    submit();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(select);

    setValue(select, 'low_intent');
    expect(confirm.getAttribute('aria-disabled')).toBeNull();
    submit();
    expect(onSubmit).toHaveBeenCalledWith('low_intent', '');
  });

  it('Confirm reject shows the Button loading state while this borrower\'s decision is on the wire (motion-08)', async () => {
    const client = new QueryClient();
    let release: () => void = () => undefined;
    const write = client.getMutationCache().build(client, {
      mutationKey: outreachMutationKeys.reject,
      mutationFn: () => new Promise<void>((resolve) => {
        release = resolve;
      }),
    });
    const onSubmit = vi.fn();
    act(() => root.render(
      <QueryClientProvider client={client}>
        <LeadRejectPanel borrowerId={BORROWER} onCancel={vi.fn()} onSubmit={onSubmit} />
      </QueryClientProvider>,
    ));
    setValue(container.querySelector('select')!, 'data_quality');
    const confirm = () => container.querySelector<HTMLButtonElement>('[data-testid="lead-reject-confirm"]')!;
    expect(confirm().classList.contains('btn--loading')).toBe(false);

    let settled: Promise<unknown> = Promise.resolve();
    await act(async () => {
      settled = write.execute({ borrowerId: BORROWER, decision: 'reject' });
      await Promise.resolve();
    });
    expect(confirm().classList.contains('btn--loading')).toBe(true);
    expect(confirm().getAttribute('aria-busy')).toBe('true');
    expect(confirm().getAttribute('aria-disabled')).toBe('true');
    expect(confirm().disabled, 'never native disabled: focus stays on the button').toBe(false);
    expect(confirm().querySelector('.btn__spinner')).not.toBeNull();
    expect(confirm().querySelector('.btn__label')?.textContent).toBe('Confirm reject');
    // A click (and the implicit submission that clicks it) is swallowed while on the wire.
    act(() => confirm().click());
    expect(onSubmit).not.toHaveBeenCalled();

    await act(async () => {
      release();
      await settled;
    });
    expect(confirm().classList.contains('btn--loading')).toBe(false);
    expect(confirm().getAttribute('aria-busy')).toBeNull();
  });

  it('another borrower\'s decision on the wire leaves this Confirm reject idle', async () => {
    const client = new QueryClient();
    const write = client.getMutationCache().build(client, {
      mutationKey: outreachMutationKeys.reject,
      mutationFn: () => new Promise<void>(() => undefined),
    });
    act(() => root.render(
      <QueryClientProvider client={client}>
        <LeadRejectPanel borrowerId={BORROWER} onCancel={vi.fn()} onSubmit={vi.fn()} />
      </QueryClientProvider>,
    ));
    await act(async () => {
      void write.execute({ borrowerId: 'B-PANELS0000002', decision: 'reject' });
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="lead-reject-confirm"]')?.classList.contains('btn--loading')).toBe(false);
  });

  it('the disposition form says a missing callback time inside the form and submits nothing', () => {
    const onSubmit = vi.fn();
    act(() => root.render(
      <LeadDispositionPanel borrowerId={BORROWER} salesTeam={TEAM} salesBusy={false} initialLo={LO_A} onCancel={vi.fn()} onSubmit={onSubmit} />,
    ));
    const [lo, outcome] = [...container.querySelectorAll('select')];
    expect(lo.value).toBe(LO_A);
    setValue(outcome, 'callback_scheduled');
    submit();

    const alert = container.querySelector('form [role="alert"]');
    expect(alert?.textContent).toBe('Callback scheduled dispositions require a callback time.');
    expect(onSubmit).not.toHaveBeenCalled();

    setValue(container.querySelector<HTMLInputElement>('input[type="datetime-local"]')!, '2026-07-15T09:30');
    submit();
    expect(container.querySelector('form [role="alert"]')).toBeNull();
    expect(onSubmit).toHaveBeenCalledWith({
      lo_email: LO_A,
      outcome: 'callback_scheduled',
      callback_at: new Date('2026-07-15T09:30').toISOString(),
    });
  });

  it('the disposition form has no free-text Notes field and sends no notes key (D-shell-deviations-g2)', () => {
    const onSubmit = vi.fn();
    act(() => root.render(
      <LeadDispositionPanel borrowerId={BORROWER} salesTeam={TEAM} salesBusy={false} initialLo={LO_A} onCancel={vi.fn()} onSubmit={onSubmit} />,
    ));
    expect(container.querySelector('form textarea')).toBeNull();
    const labels = [...container.querySelectorAll('.field__label')].map((label) => label.textContent);
    expect(labels).toEqual(['Loan officer', 'Outcome']);
    submit();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(Object.keys(onSubmit.mock.calls[0][0] as object).sort()).toEqual(['callback_at', 'lo_email', 'outcome']);
  });

  it('the disposition form asks for a loan officer when none is chosen', () => {
    const onSubmit = vi.fn();
    act(() => root.render(
      <LeadDispositionPanel borrowerId={BORROWER} salesTeam={TEAM} salesBusy={false} initialLo="" onCancel={vi.fn()} onSubmit={onSubmit} />,
    ));
    submit();
    expect(container.querySelector('form [role="alert"]')?.textContent).toBe('Choose the loan officer who worked this lead.');
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
