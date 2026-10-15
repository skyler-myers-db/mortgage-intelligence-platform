/**
 * @vitest-environment happy-dom
 *
 * The actor's saved-view preferences (W5e item 2b): a PRIVATE_LOCAL key read
 * and written only through lib/actorScope. A parse failure or another
 * actor's stamp reads as empty and the read writes nothing; at most three
 * recent views; reconcile follows the listed views; a delete forgets.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { ACTOR_A, ACTOR_B } from '../test/actorKeys';
import { installLocalStorage } from '../test/installLocalStorage';
import { ACTOR_SCOPE_REGISTRY, _resetActorScopeForTests } from '../lib/actorScope';
import {
  EMPTY_SAVED_VIEW_PREFS,
  SAVED_VIEW_PREFS_KEY,
  forgetSavedView,
  parseSavedViewPrefs,
  readSavedViewPrefs,
  reconcileSavedViewPrefs,
  recordRecentSavedView,
  setDefaultSavedView,
} from './lead-queue.savedViewPrefs';

const ref = (id: string, name = `View ${id}`, params = `state=IL&view=sales-ops&hide=${id === 'a' ? 'rate' : 'segments'}`) => ({ id, name, params });

beforeEach(() => {
  installLocalStorage();
  _resetActorScopeForTests({ status: 'open', owner: ACTOR_A });
});

describe('saved-view preferences', () => {
  it('is a registered PRIVATE_LOCAL key', () => {
    expect(SAVED_VIEW_PREFS_KEY).toBe('mip.leadQueue.savedViews.v1');
    expect(ACTOR_SCOPE_REGISTRY.PRIVATE_LOCAL).toContain(SAVED_VIEW_PREFS_KEY);
  });

  it('sets and clears the default, stamped v1 JSON', () => {
    setDefaultSavedView(ref('a'));
    expect(readSavedViewPrefs().defaultView).toEqual(ref('a'));
    expect(JSON.parse(window.localStorage.getItem(SAVED_VIEW_PREFS_KEY)!)).toEqual({ v: 1, defaultView: ref('a'), recent: [] });
    setDefaultSavedView(null);
    expect(readSavedViewPrefs()).toEqual(EMPTY_SAVED_VIEW_PREFS);
    expect(window.localStorage.getItem(SAVED_VIEW_PREFS_KEY)).toBeNull();
  });

  it('keeps at most three recent views, most recent first, de-duplicated', () => {
    for (const id of ['a', 'b', 'c', 'd', 'b']) recordRecentSavedView(ref(id));
    expect(readSavedViewPrefs().recent.map((entry) => entry.id)).toEqual(['b', 'd', 'c']);
  });

  it('reads a value that does not parse as empty, and the read writes nothing', () => {
    window.localStorage.setItem(SAVED_VIEW_PREFS_KEY, '{not json');
    _resetActorScopeForTests({ status: 'open', owner: ACTOR_A });
    expect(readSavedViewPrefs()).toEqual(EMPTY_SAVED_VIEW_PREFS);
    expect(window.localStorage.getItem(SAVED_VIEW_PREFS_KEY)).toBe('{not json');
    expect(parseSavedViewPrefs(JSON.stringify({ v: 2, defaultView: ref('a'), recent: [] }))).toEqual(EMPTY_SAVED_VIEW_PREFS);
    expect(parseSavedViewPrefs(JSON.stringify({ v: 1, defaultView: { id: 1 }, recent: [ref('a'), 'x'] })))
      .toEqual({ defaultView: null, recent: [ref('a')] });
  });

  it("reads another actor's stored preferences as empty under a foreign stamp", () => {
    setDefaultSavedView(ref('a'));
    window.localStorage.setItem('mip.actorOwner', ACTOR_B);
    _resetActorScopeForTests({ status: 'open', owner: ACTOR_A });
    window.localStorage.setItem('mip.actorOwner', ACTOR_B);
    expect(readSavedViewPrefs()).toEqual(EMPTY_SAVED_VIEW_PREFS);
  });

  it('reconciles with the listed views: drops unlisted ones, refreshes names and params, writes only on a change', () => {
    setDefaultSavedView(ref('a'));
    recordRecentSavedView(ref('b'));
    recordRecentSavedView(ref('a'));
    const before = window.localStorage.getItem(SAVED_VIEW_PREFS_KEY);
    reconcileSavedViewPrefs([ref('a'), ref('b')]);
    expect(window.localStorage.getItem(SAVED_VIEW_PREFS_KEY)).toBe(before);

    reconcileSavedViewPrefs([ref('a', 'Renamed', 'state=TX')]);
    expect(readSavedViewPrefs()).toEqual({
      defaultView: ref('a', 'Renamed', 'state=TX'),
      recent: [ref('a', 'Renamed', 'state=TX')],
    });
    reconcileSavedViewPrefs([]);
    expect(readSavedViewPrefs()).toEqual(EMPTY_SAVED_VIEW_PREFS);
  });

  it('forgets a deleted view from both the default and the recent list', () => {
    setDefaultSavedView(ref('a'));
    recordRecentSavedView(ref('a'));
    recordRecentSavedView(ref('b'));
    forgetSavedView('a');
    expect(readSavedViewPrefs()).toEqual({ defaultView: null, recent: [ref('b')] });
  });
});
