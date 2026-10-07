/**
 * @vitest-environment happy-dom
 *
 * The local-area over-clear, RULED fail-safe (W5b identity-reset deferred[0];
 * W5a identity-foundation review0 NB[2]; delegated-owner default, W5d). This
 * pins TODAY's behaviour so a future narrowing is a deliberate change, not a
 * drift: docs/security/identity-boundary.md records the ruling.
 *
 * A FIRST observation (no document owner yet) of actor k, over a local area
 * stamped for ANOTHER actor (whose pins it holds) and a session area stamped
 * k (holding k's own Genie transcript): row 3 removes the other actor's local
 * data and emits 'cleared'; row 2 keeps k's session data. The 'cleared'
 * dispatches the Genie reset (lib/genieConversation), whose listener
 * (lib/genieInFlightTurn) clears the transcript through the open gate, so k
 * loses its own session transcript too. Fail-safe, not a leak: nothing of the
 * other actor is ever shown, and k only re-asks.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ACTOR_A, ACTOR_B } from '../test/actorKeys';
import { installLocalStorage } from '../test/installLocalStorage';
import {
  ACTOR_SCOPE_REGISTRY,
  NOBODY,
  _resetActorScopeForTests,
  actorScopeStatus,
  observeActor,
  subscribeActorScope,
  type ActorScopeReason,
} from './actorScope';
import { GENIE_CONVERSATION_RESET_EVENT } from './genieConversation';
import { GENIE_CONVERSATION_TURNS_KEY, getGenieTurns } from './genieConversationStore';
import './genieInFlightTurn';
import { PINNED_INSIGHTS_KEY } from './pinnedInsights';

const { STAMPS } = ACTOR_SCOPE_REGISTRY;
const OWN_TRANSCRIPT = JSON.stringify([
  { question: 'k asked this', response: { answer: 'k was answered', source: 'genie' } },
]);
const OTHER_PINS = JSON.stringify([{ id: 'p1', question: 'other actor pin', summary: 's' }]);

const cleanups: Array<() => void> = [];

beforeEach(() => {
  _resetActorScopeForTests({ status: 'pending', owner: NOBODY });
  installLocalStorage();
  window.sessionStorage.clear();
});

afterEach(() => {
  while (cleanups.length > 0) cleanups.pop()?.();
  _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  window.sessionStorage.clear();
});

describe('the local-area over-clear (ruled fail-safe; the event contract is unchanged)', () => {
  it("a first observation of k over another actor's local stamp clears, and the Genie reset removes k's own transcript", () => {
    window.localStorage.setItem(STAMPS.local, ACTOR_B);
    window.localStorage.setItem(PINNED_INSIGHTS_KEY, OTHER_PINS);
    window.sessionStorage.setItem(STAMPS.session, ACTOR_A);
    window.sessionStorage.setItem(GENIE_CONVERSATION_TURNS_KEY, OWN_TRANSCRIPT);
    const reasons: ActorScopeReason[] = [];
    let resets = 0;
    const onReset = () => {
      resets += 1;
    };
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, onReset);
    cleanups.push(() => window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, onReset));
    cleanups.push(subscribeActorScope(({ reason }) => reasons.push(reason)));

    observeActor({ key: ACTOR_A });

    expect(actorScopeStatus()).toBe('open');
    expect(reasons).toEqual(['cleared', 'restamped', 'opened']);
    expect(resets, "the 'cleared' dispatched one Genie reset").toBe(1);
    expect(window.localStorage.getItem(PINNED_INSIGHTS_KEY), "the other actor's pins are gone").toBeNull();
    expect([window.localStorage.getItem(STAMPS.local), window.sessionStorage.getItem(STAMPS.session)]).toEqual([ACTOR_A, ACTOR_A]);
    expect(window.sessionStorage.getItem(GENIE_CONVERSATION_TURNS_KEY), "k's own transcript went with the reset").toBeNull();
    expect(getGenieTurns()).toEqual([]);
  });

  it('control: with both areas already stamped k, nothing clears and the transcript stays', () => {
    window.localStorage.setItem(STAMPS.local, ACTOR_A);
    window.sessionStorage.setItem(STAMPS.session, ACTOR_A);
    window.sessionStorage.setItem(GENIE_CONVERSATION_TURNS_KEY, OWN_TRANSCRIPT);
    const reasons: ActorScopeReason[] = [];
    cleanups.push(subscribeActorScope(({ reason }) => reasons.push(reason)));

    observeActor({ key: ACTOR_A });

    expect(reasons).toEqual(['opened']);
    expect(window.sessionStorage.getItem(GENIE_CONVERSATION_TURNS_KEY)).toBe(OWN_TRANSCRIPT);
    expect(getGenieTurns().map((turn) => turn.question)).toEqual(['k asked this']);
  });
});
