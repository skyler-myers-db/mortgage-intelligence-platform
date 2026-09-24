/**
 * Offer Orchestrator lane fixtures (audit critic-02): governed draft copy for
 * the certified-copy preview. Not default fixtures: specs register them per
 * test over `POST /api/outreach/draft` with `registerDraftCopy`.
 *
 * `POST /api/outreach/draft` writes a DRAFT_OUTREACH audit row in the real
 * backend; specs only trigger it the way a user does (opening the route,
 * choosing a channel), never from hover, prefetch or polling.
 */
import type { OutreachDraftResult } from '../../../../src/lib/apiTypes';
import type { MockApi } from '../mockApi';
import { outreachDraftFor } from './offers';
import { LENDER_NAME } from './reference';

/** 90 characters: about four times what the old read-only input showed. */
export const LONG_SUBJECT = 'A quick review of your mortgage options before rates move again this year at no obligation';

/** A governed SMS in the GSM-7 alphabet: one segment. */
export const GSM_SMS_BODY = `${LENDER_NAME}: mortgage review. Reply YES. Msg&data rates may apply. Reply STOP to opt out.`;

/** The same SMS with one curly apostrophe: the whole message becomes UCS-2. */
export const UCS2_SMS_BODY = `${GSM_SMS_BODY} We’re here to help.`;

/**
 * A governed email body whose sign-off keeps a single line break inside a
 * paragraph: the certified copy must paint it as a break, not a space.
 */
export const EMAIL_BODY = [
  'Hello,',
  'Based on current market rates and your estimated home equity, a mortgage review may lower your monthly cost. A loan officer can walk you through the numbers, with no obligation.',
  `Thank you,\n${LENDER_NAME} · NMLS #000000 · Equal Housing Lender`,
].join('\n\n');

/**
 * The same email with three blank lines after the greeting: the body splits
 * into an empty paragraph there, which must still paint as blank space.
 */
export const EMAIL_BODY_EXTRA_BLANK_LINES = EMAIL_BODY.replace('Hello,\n\n', 'Hello,\n\n\n\n');

interface DraftCopy {
  subject?: string;
  emailBody?: string;
  smsBody?: string;
}

/** The last draft served per channel: what the page must paint, exactly. */
export type ServedDrafts = Partial<Record<OutreachDraftResult['channel'], OutreachDraftResult>>;

/** Serve the default governed draft with this lane's subject and SMS body. */
export function registerDraftCopy(mockApi: MockApi, copy: DraftCopy): ServedDrafts {
  const served: ServedDrafts = {};
  mockApi.register<OutreachDraftResult>('POST', '/api/outreach/draft', (request) => {
    const base = outreachDraftFor(request);
    const draft = base.channel === 'sms'
      ? { ...base, subject: null, body: copy.smsBody ?? base.body }
      : { ...base, subject: copy.subject ?? base.subject, body: copy.emailBody ?? base.body };
    served[draft.channel] = draft;
    return { body: draft };
  });
  return served;
}
