// @vitest-environment happy-dom
/**
 * Virtual screen-reader pass: a form with validation errors, the Request
 * approval form (WCAG 3.3.1, 3.3.2, 3.3.3, 4.1.2, 4.1.3;
 * docs/accessibility/VPAT.md). Harness as RequestApprovalForm.test.tsx: the
 * create call is spied and refused with the governed-text 422, so the form
 * shows its fixed failure copy. Synthetic borrower ids only.
 *
 * Not modelled (virtualReader.ts): an INSERTED role="alert" element. The
 * reader speaks only mutations inside an existing live region, so the
 * failure paragraph, which is inserted with role="alert", is silent here
 * although browsers raise an alert event for it. The suite therefore pins
 * the alert on the DOM, and records what the reader does speak: the field
 * re-announced as invalid with the failure as its description, and the
 * alert read in place by the virtual cursor. Whether NVDA / VoiceOver speak
 * the inserted alert is an open item for the person's pass (VPAT). The
 * reader also never speaks a <textarea>'s typed text.
 *
 * Found by this pass and fixed with it: the wrapping <label> holds the hint
 * too, so the field's name used to be "Reason for the request Say why these
 * borrowers; ..." with the hint read again as its description. The textarea
 * is now named by the label text alone (aria-labelledby), as in
 * RevokeApprovalPanel.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { approvalRequestsApi } from '../../lib/apiClients/approvalRequests';
import { ApiError } from '../../lib/apiTransport';
import { clearToasts } from '../../lib/toast';
import { mount } from '../render';
import { expectTranscript, nextUntil, press, reportFocus, settle, startReader } from './virtualReader';
import { REQUEST_FAILURE_COPY, RequestApprovalForm } from '../../components/mortgage/RequestApprovalForm';

const IDS = ['B-REQFORM000001', 'B-REQFORM000002'];

beforeAll(async () => {
  await import('../../components/ui/AsyncFailure');
}, 60_000);

afterEach(() => {
  vi.restoreAllMocks();
  act(() => clearToasts());
});

const field = () => document.querySelector<HTMLTextAreaElement>('[data-testid="request-approval-reason"]')!;
const send = () => document.querySelector<HTMLButtonElement>('[data-testid="request-approval-send"]')!;

describe('virtual screen reader: form validation', () => {
  it('labels the field, refuses an empty send by focus, and reads a server refusal as an invalid field and an alert', async () => {
    const create = vi.spyOn(approvalRequestsApi, 'create').mockRejectedValue(
      new ApiError('rationale failed the governed text policy', { path: '/api/v1/outreach/approval-requests', status: 422 }),
    );
    const reader = await startReader();

    const opened = await reader.step('The form opens with focus in its reason field', async () => {
      await mount(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <RequestApprovalForm borrowerIds={IDS} onCancel={vi.fn()} onRequested={vi.fn()} />
        </QueryClientProvider>,
      );
    });
    expect(document.activeElement).toBe(field());
    expect(opened.at(-1)).toBe('textbox, Reason for the request, Say why these borrowers; leave out personal details., required');

    const empty = await reader.step('Tab to Send request while the reason is empty', async () => {
      await press('Tab');
      await press('Tab');
    });
    expect(document.activeElement).toBe(send());
    expect(empty.at(-1)).toBe('button, Send request, disabled');

    const refused = await reader.step('Press Send request: nothing is sent and focus returns to the field', async () => {
      await act(async () => send().click());
      await settle();
    });
    expect(create).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(field());
    expect(refused).toEqual(['textbox, Reason for the request, Say why these borrowers; leave out personal details., required']);

    const failed = await reader.step('Write a reason and send; the server refuses it (governed text, 422)', async () => {
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field(), 'Spring refinance review for the Illinois cohort.');
        field().dispatchEvent(new Event('input', { bubbles: true }));
      });
      await act(async () => send().click());
      await vi.waitFor(async () => {
        await settle(1);
        expect(document.querySelector('[data-testid="request-approval-failure"]')).not.toBeNull();
      }, { timeout: 5000 });
      await settle();
    });
    expect(create).toHaveBeenCalledTimes(1);
    // The inserted alert is not spoken by this reader (see the header).
    expect(failed.some((phrase) => phrase.includes(REQUEST_FAILURE_COPY.governed))).toBe(false);
    const alert = document.querySelector('[data-testid="request-approval-failure"]')!;
    expect(alert.getAttribute('role')).toBe('alert');
    expect(alert.textContent).toBe(REQUEST_FAILURE_COPY.governed);
    expect(field().getAttribute('aria-invalid')).toBe('true');
    expect(field().getAttribute('aria-describedby')?.split(' ')).toContain(alert.id);

    const invalid = await reader.step('Return to the field: it is invalid and described by the failure', async () => {
      await act(async () => field().focus());
      await settle();
      await reportFocus();
    });
    // Named by its label alone (the hint is said once, as the description,
    // with the failure after it). The reader never speaks a <textarea>'s typed
    // text (it models input and select values only); a real reader does.
    expect(invalid.at(-1)).toBe(
      `textbox, Reason for the request, Say why these borrowers; leave out personal details. ${REQUEST_FAILURE_COPY.governed}, invalid, required`,
    );

    const read = await reader.step('Read on to the alert with the virtual cursor', async () => {
      await nextUntil(/^end of alert/);
    });
    expect(read.slice(-3)).toEqual(['alert', REQUEST_FAILURE_COPY.governed, 'end of alert']);

    await expectTranscript('form-validation', reader);
  }, 30_000);
});
