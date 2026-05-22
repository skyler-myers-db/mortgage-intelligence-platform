import { describe, expect, it, vi } from 'vitest';
import { resetActorScopedAppState, shouldInstallRum } from './AppContext';

type ResetSetters = Parameters<typeof resetActorScopedAppState>[0];

function setter<K extends keyof ResetSetters>() {
  return vi.fn() as unknown as ResetSetters[K];
}

describe('resetActorScopedAppState', () => {
  it('clears every actor-scoped workspace and workflow surface', () => {
    const setWorkspaceReloadToken = setter<'setWorkspaceReloadToken'>();
    const setters: ResetSetters = {
      setApprovals: setter<'setApprovals'>(),
      setDrawer: setter<'setDrawer'>(),
      setGenieOpen: setter<'setGenieOpen'>(),
      setLastBorrowerIdState: setter<'setLastBorrowerIdState'>(),
      setSavedLeads: setter<'setSavedLeads'>(),
      setSavedDrafts: setter<'setSavedDrafts'>(),
      setWorkspaceStatus: setter<'setWorkspaceStatus'>(),
      setWorkspaceError: setter<'setWorkspaceError'>(),
      setWorkspaceReloadToken,
    };

    resetActorScopedAppState(setters);

    expect(setters.setApprovals).toHaveBeenCalledWith({});
    expect(setters.setDrawer).toHaveBeenCalledWith(null);
    expect(setters.setGenieOpen).toHaveBeenCalledWith(false);
    expect(setters.setLastBorrowerIdState).toHaveBeenCalledWith(null);
    expect(setters.setSavedLeads).toHaveBeenCalledWith({});
    expect(setters.setSavedDrafts).toHaveBeenCalledWith({});
    expect(setters.setWorkspaceStatus).toHaveBeenCalledWith('loading');
    expect(setters.setWorkspaceError).toHaveBeenCalledWith(null);

    const reloadArg = vi.mocked(setWorkspaceReloadToken).mock.calls[0]?.[0];
    expect(typeof reloadArg).toBe('function');
    expect((reloadArg as (n: number) => number)(41)).toBe(42);
  });
});

describe('shouldInstallRum', () => {
  it('treats browser RUM as explicit opt-in configuration', () => {
    expect(shouldInstallRum(undefined)).toBe(false);
    expect(shouldInstallRum({ rum_enabled: false })).toBe(false);
    expect(shouldInstallRum({ rum_enabled: true })).toBe(true);
  });
});
