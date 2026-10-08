/**
 * @vitest-environment happy-dom
 *
 * The Offer's failures in buyer-safe copy (2026-09-21 audit states-04): a
 * lead phrase plus the shared described sentence, never `err.message`; the
 * route's own fixed sentences shown as written; a 409 or 422 shows a reviewed
 * refusal sentence or a neutral one, never describeApiError's reload or
 * filter advice; only a draft-proof refusal offers an explicit Regenerate
 * draft (never automatic, never a reload).
 */
import { act } from 'react';
import { MemoryRouter } from 'react-router';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { preloadDescribedError } from '../components/ui/DescribedError';
import { ApiError } from '../lib/api';
import { mount } from '../test/render';
import { OFFER_REFUSED } from '../lib/offerRefusals';
import { OfferFailureBody, OfferWriteFailureCopy } from './offer-orchestrator.errors';
import { FixedCopyError, OFFER_SNAPSHOT_MISMATCH_MESSAGE, OfferSnapshotMismatchError } from './offer-orchestrator.queries';
import { OfferLoadErrorRoute } from './offer-orchestrator.route-ui';

const RAW = 'audit write failed: psycopg OperationalError at 10.0.0.12';

function apiError(status: number, detail = RAW): ApiError {
  return new ApiError(detail, { path: '/api/outreach/approve', status });
}

const DRAFT_PROOF_409 = 'Generated draft proof does not match this approval; regenerate the draft.';
const SAVE_PROOF_409 = 'Generated draft no longer matches the reviewed copy; regenerate before saving.';
const FREQUENCY_CAP_409 = 'borrower hit frequency cap; earliest re-contact 2026-11-02';
const GOVERNED_TEXT_422 = 'rationale failed the governed text policy';

const regenerateButton = (container: HTMLElement) =>
  [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('Regenerate draft'));

beforeAll(async () => {
  await preloadDescribedError();
  await import('../components/mortgage/OfferFailureSentence');
}, 60_000);

/** The described sentence's chunk, then its vocabulary, load on the first failure. */
async function settle(): Promise<void> {
  for (let round = 0; round < 3; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

describe('Offer write failures (states-04)', () => {
  it('a 500 keeps the lead phrase and the described sentence, never the raw message', async () => {
    const { container } = await mount(
      <OfferWriteFailureCopy failure={{ lead: "Couldn't write approval.", cause: apiError(500) }} onRegenerate={vi.fn()} />,
    );
    await settle();
    expect(container.textContent).toBe("Couldn't write approval. The server hit an unexpected error.");
    expect(container.textContent).not.toContain('psycopg');
    expect(container.querySelector('button')).toBeNull();
  });

  it('a draft-proof 409 on approve offers Regenerate draft, run only on the reviewer\'s click', async () => {
    const onRegenerate = vi.fn();
    const { container } = await mount(
      <OfferWriteFailureCopy
        failure={{ lead: "Couldn't write approval.", cause: apiError(409, DRAFT_PROOF_409) }}
        onRegenerate={onRegenerate}
      />,
    );
    await settle();
    expect(container.textContent).toContain(`Couldn't write approval. ${DRAFT_PROOF_409}`);
    expect(container.textContent).not.toMatch(/reload/i);
    const regenerate = regenerateButton(container);
    expect(regenerate).toBeDefined();
    expect(onRegenerate).not.toHaveBeenCalled();
    act(() => regenerate?.click());
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it('a frequency-cap 409 on approve reads the cap, with no Regenerate draft and no stale-draft claim', async () => {
    const { container } = await mount(
      <OfferWriteFailureCopy
        failure={{ lead: "Couldn't write approval.", cause: apiError(409, FREQUENCY_CAP_409) }}
        onRegenerate={vi.fn()}
      />,
    );
    await settle();
    expect(container.textContent).toBe("Couldn't write approval. Borrower hit frequency cap; earliest re-contact 2026-11-02.");
    expect(container.textContent).not.toMatch(/no longer current|reload/i);
    expect(regenerateButton(container)).toBeUndefined();
  });

  it('a 422 governed-text refusal of the reject note names the field, never filter advice', async () => {
    const { container } = await mount(
      <OfferWriteFailureCopy
        failure={{ lead: "Couldn't record rejection.", cause: apiError(422, GOVERNED_TEXT_422) }}
        onRegenerate={vi.fn()}
      />,
    );
    await settle();
    expect(container.textContent).toBe("Couldn't record rejection. Rationale failed the governed text policy.");
    expect(container.textContent).not.toMatch(/filter/i);
    expect(regenerateButton(container)).toBeUndefined();
  });

  it('a draft-proof 409 on save offers Regenerate draft and never reload advice', async () => {
    const onRegenerate = vi.fn();
    const { container } = await mount(
      <OfferWriteFailureCopy
        failure={{ lead: "Couldn't save draft.", cause: apiError(409, SAVE_PROOF_409) }}
        subject="the draft"
        scope="draft-save"
        onRegenerate={onRegenerate}
      />,
    );
    await settle();
    expect(container.textContent).toContain(`Couldn't save draft. ${SAVE_PROOF_409}`);
    expect(container.textContent).not.toMatch(/reload/i);
    act(() => regenerateButton(container)?.click());
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['an unreviewed 409 on approve', "Couldn't write approval.", apiError(409)],
    ['the review-mode 422 ("Reload the app to approve")', "Couldn't write approval.", apiError(422, 'Reload the app to approve')],
    ['a schema 422 with issues', "Couldn't record rejection.", new ApiError('rationale: String too long', {
      path: '/api/outreach/reject',
      status: 422,
      validationIssues: [{ field: 'rationale', location: ['body', 'rationale'], message: 'String too long' }],
    })],
    ['a governed-text shape carrying text', "Couldn't record rejection.", apiError(422, `${GOVERNED_TEXT_422}: Jane Doe`)],
    ['a frequency cap without a date', "Couldn't write approval.", apiError(409, 'borrower hit frequency cap; earliest re-contact soon')],
  ])('%s reads the neutral sentence: no cause, no advice, no raw detail', async (_name, lead, cause) => {
    const { container } = await mount(<OfferWriteFailureCopy failure={{ lead, cause }} onRegenerate={vi.fn()} />);
    await settle();
    expect(container.textContent).toBe(`${lead} ${OFFER_REFUSED.decision}`);
    expect(container.textContent).not.toMatch(/reload|filter|psycopg|Jane|soon|too long/i);
    expect(regenerateButton(container)).toBeUndefined();
  });

  it('an unreviewed refusal of a save reads the save\'s neutral sentence', async () => {
    const { container } = await mount(
      <OfferWriteFailureCopy
        failure={{ lead: "Couldn't save draft.", cause: apiError(409) }}
        subject="the draft"
        scope="draft-save"
        onRegenerate={vi.fn()}
      />,
    );
    await settle();
    expect(container.textContent).toBe(`Couldn't save draft. ${OFFER_REFUSED['draft-save']}`);
    expect(regenerateButton(container)).toBeUndefined();
  });

  it('a refusal of a read shows its reviewed sentence or the request\'s neutral one', async () => {
    const eligibility = await mount(
      <OfferFailureBody cause={apiError(422, 'borrower is not marketing-eligible: consent_status=opted_out')} subject="the offer draft" />,
    );
    const unreviewed = await mount(<OfferFailureBody cause={apiError(409)} subject="the offer draft" />);
    await settle();
    expect(eligibility.container.textContent).toBe('Borrower is not marketing-eligible: consent status=opted out.');
    expect(unreviewed.container.textContent).toBe(OFFER_REFUSED.request);
  });

  it('shows the route\'s own fixed sentences as written', async () => {
    const stale = new FixedCopyError('Campaign variant proof is stale. Reopen the saved campaign before approval.');
    const { container } = await mount(<OfferFailureBody cause={stale} subject="the offer draft" />);
    expect(container.textContent).toBe('Campaign variant proof is stale. Reopen the saved campaign before approval.');
    expect(new OfferSnapshotMismatchError()).toBeInstanceOf(FixedCopyError);
  });
});

describe('the Offer load failure route (states-04)', () => {
  const render = (props: { notFound?: boolean; cause?: unknown }) => mount(
    <MemoryRouter>
      <OfferLoadErrorRoute
        borrowerId="B-0000000000001"
        loadError="Couldn't load borrower or offer."
        loadErrorCause={props.cause ?? apiError(500)}
        notFound={props.notFound ?? false}
        onRetry={vi.fn()}
        backTo="/lead-queue"
      />
    </MemoryRouter>,
  );

  it('leads with the phrase and describes the cause; Retry and the queue link stay', async () => {
    const { container } = await render({});
    await settle();
    const lede = container.querySelector('.lede')?.textContent ?? '';
    expect(lede).toBe("Couldn't load borrower or offer. The server hit an unexpected error.");
    expect(container.textContent).not.toContain('psycopg');
    expect(container.querySelector('button[aria-label="Retry loading borrower and offer"]')).not.toBeNull();
    expect(container.querySelector('a[href="/lead-queue"]')?.textContent).toBe('Back to lead queue');
  });

  it('keeps the snapshot-mismatch sentence, which tells the reviewer what to do', async () => {
    const { container } = await render({ cause: new OfferSnapshotMismatchError() });
    expect(container.querySelector('.lede')?.textContent).toBe(`Couldn't load borrower or offer. ${OFFER_SNAPSHOT_MISMATCH_MESSAGE}`);
  });

  it('the not-found path is unchanged', async () => {
    const { container } = await render({ notFound: true });
    expect(container.querySelector('.lede')?.textContent).toBe(
      'Borrower B-0000000000001 was not found. Check the ID, use search, or return to the lead queue.',
    );
  });
});
