import { beforeEach, describe, expect, it } from 'vitest';
import { clearActorScopedMemoryCaches } from './actorScopedMemoryCaches';
import {
  MAX_GENIE_ANSWER_MEMORY,
  beginGenieExport,
  beginGenieVote,
  clearGenieAnswerMemory,
  genieAnswerMemorySize,
  genieExportLatchKey,
  genieVoteKey,
  settleGenieExport,
  settleGenieVote,
} from './genieAnswerMemory';

/**
 * The Genie answer memory (audit 2026-09-21 `genie-08` item 3): votes and
 * CSV export latches that outlive the component, cleared at the actor
 * boundary, capped, and holding only ids and fixed copy.
 */

const VOTE = genieVoteKey('conv-1', 'msg-1');
const LATCH = genieExportLatchKey({ conversationId: 'conv-1', messageId: 'msg-1', scope: 'answer', sectionIndex: null });

let minted = 0;
const mint = () => {
  minted += 1;
  return `request-${minted}`;
};

describe('genieAnswerMemory', () => {
  beforeEach(() => {
    clearGenieAnswerMemory();
    minted = 0;
  });

  it('keys votes by answer and latches by rows block, section included', () => {
    expect(VOTE).toBe('vote|conv-1:msg-1');
    expect(LATCH).toBe('csv|conv-1:msg-1:answer:-');
    expect(genieExportLatchKey({ conversationId: 'c', messageId: 'm', scope: 'section', sectionIndex: 2 })).toBe('csv|c:m:section:2');
  });

  it('mints one request id per direction and never a second: a retry reuses it', () => {
    const first = beginGenieVote(VOTE, 'up', mint)!;
    expect(first.requestId).toBe('request-1');
    // Pending: the latch holds a second vote in either direction.
    expect(beginGenieVote(VOTE, 'up', mint)).toBeNull();
    expect(beginGenieVote(VOTE, 'down', mint)).toBeNull();
    expect(settleGenieVote(VOTE, first, false)).toBe(true);

    const retry = beginGenieVote(VOTE, 'up', mint)!;
    expect(retry.requestId).toBe('request-1');
    expect(settleGenieVote(VOTE, retry, true)).toBe(true);
    // Recorded: never offered again.
    expect(beginGenieVote(VOTE, 'down', mint)).toBeNull();
    expect(minted).toBe(1);
  });

  it('the actor boundary empties both kinds, and a request that settles after it writes nothing', () => {
    const vote = beginGenieVote(VOTE, 'up', mint)!;
    const exportEpoch = beginGenieExport(LATCH)!;
    expect(beginGenieExport(LATCH)).toBeNull();
    expect(genieAnswerMemorySize()).toBe(2);

    clearActorScopedMemoryCaches();

    expect(genieAnswerMemorySize()).toBe(0);
    expect(settleGenieVote(VOTE, vote, true)).toBe(false);
    expect(settleGenieExport(LATCH, exportEpoch, 'CSV downloaded.')).toBe(false);
    expect(genieAnswerMemorySize()).toBe(0);
    // The next actor starts clean: a fresh id, a free latch.
    expect(beginGenieVote(VOTE, 'up', mint)?.requestId).toBe('request-2');
    expect(beginGenieExport(LATCH)).not.toBeNull();
  });

  it(`holds at most ${MAX_GENIE_ANSWER_MEMORY} entries, oldest out`, () => {
    for (let i = 0; i <= MAX_GENIE_ANSWER_MEMORY; i += 1) {
      beginGenieVote(genieVoteKey('conv', `msg-${i}`), 'up', mint);
    }
    expect(genieAnswerMemorySize()).toBe(MAX_GENIE_ANSWER_MEMORY);
    // msg-0 was evicted: voting on it again starts over; msg-1 is still pending.
    expect(beginGenieVote(genieVoteKey('conv', 'msg-1'), 'up', mint)).toBeNull();
    expect(beginGenieVote(genieVoteKey('conv', 'msg-0'), 'up', mint)).not.toBeNull();
  });
});
