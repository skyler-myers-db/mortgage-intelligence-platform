/**
 * One idempotency `request_id` per user INTENT (audit stack-09 / tables-05).
 *
 * The governed write endpoints (approve, reject, assign, distribute,
 * disposition) replay a matching `request_id` without writing a second audit
 * row. That protection only helps when a retry of the SAME intent reuses the
 * id: "Confirm again" after a 500 or a timeout must send the id the first
 * click sent, while a changed intent (a new draft, another reason code, a
 * different set of borrowers) must mint a new one or the server would refuse
 * it as a different request under a used id.
 *
 * A caller names the intent with a fingerprint string; `idFor` returns the
 * id minted for it (minting once), and `settle` forgets it once the write
 * succeeded, so a later identical intent is a new request.
 *
 * The map is a ref: ids are read and written only from event handlers, never
 * during render.
 */
import { useRef } from 'react';
import { _newRequestId } from '../apiTransport';

export interface IntentRequestIds {
  /** The request id for this intent, minted on first use. */
  idFor(fingerprint: string): string;
  /** The intent's write succeeded: its next occurrence is a new request. */
  settle(fingerprint: string): void;
}

/** A stable fingerprint for an intent's identifying parts. */
export function intentFingerprint(...parts: ReadonlyArray<string | number | null | undefined>): string {
  return JSON.stringify(parts.map((part) => part ?? null));
}

export function useIntentRequestIds(): IntentRequestIds {
  const idsRef = useRef<Map<string, string> | null>(null);
  const ids = (): Map<string, string> => {
    if (idsRef.current === null) idsRef.current = new Map();
    return idsRef.current;
  };
  return {
    idFor(fingerprint) {
      const map = ids();
      const existing = map.get(fingerprint);
      if (existing !== undefined) return existing;
      const minted = _newRequestId();
      map.set(fingerprint, minted);
      return minted;
    },
    settle(fingerprint) {
      ids().delete(fingerprint);
    },
  };
}
