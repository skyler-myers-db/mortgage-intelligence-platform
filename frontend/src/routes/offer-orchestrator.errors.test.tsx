/**
 * @vitest-environment happy-dom
 *
 * The Offer's failures in buyer-safe copy (2026-09-21 audit states-04): a
 * lead phrase plus the shared described sentence, never `err.message`; the
 * route's own fixed sentences shown as written; a 409 on a decision offers an
 * explicit Regenerate draft (never automatic, never a reload).
 */
import { act } from 'react';
import { MemoryRouter } from 'react-router';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { preloadDescribedError } from '../components/ui/DescribedError';
import { ApiError } from '../lib/api';
import { mount } from '../test/render';
import { OFFER_CONFLICT_BODY, OfferFailureBody, OfferWriteFailureCopy } from './offer-orchestrator.errors';
import { FixedCopyError, OFFER_SNAPSHOT_MISMATCH_MESSAGE, OfferSnapshotMismatchError } from './offer-orchestrator.queries';
import { OfferLoadErrorRoute } from './offer-orchestrator.route-ui';

const RAW = 'audit write failed: psycopg OperationalError at 10.0.0.12';

function apiError(status: number): ApiError {
  return new ApiError(RAW, { path: '/api/outreach/approve', status });
}

beforeAll(async () => {
  await preloadDescribedError();
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

  it('a 409 offers Regenerate draft, run only on the reviewer\'s click', async () => {
    const onRegenerate = vi.fn();
    const { container } = await mount(
      <OfferWriteFailureCopy failure={{ lead: "Couldn't record rejection.", cause: apiError(409) }} onRegenerate={onRegenerate} />,
    );
    await settle();
    expect(container.textContent).toContain(`Couldn't record rejection. ${OFFER_CONFLICT_BODY}`);
    expect(container.textContent).not.toMatch(/reload/i);
    expect(container.textContent).not.toContain('psycopg');
    const regenerate = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('Regenerate draft'));
    expect(regenerate).toBeDefined();
    expect(onRegenerate).not.toHaveBeenCalled();
    act(() => regenerate?.click());
    expect(onRegenerate).toHaveBeenCalledTimes(1);
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
