import { createContext, type SetStateAction } from 'react';
import type { DrawerSource } from './AppContext';

/**
 * The AppContext selector store (2026-09-21 audit runtime-05).
 *
 * One 34-field context value re-rendered every `useApp()` consumer on every
 * change, including the evidence chip and confidence meter in each Lead
 * Queue row: expanding a row (setLastBorrowerId) or opening the evidence
 * drawer (setDrawer) paid for the whole shell. The hot fields live here, in
 * a plain external store read with useSyncExternalStore, and `useApp(...keys)`
 * (AppContext.tsx) subscribes a component to exactly the keys it names.
 * AppProvider still builds the full facade from the same store, so
 * `useApp()` and every test mock of it keep working unchanged.
 *
 * The actions are created once and never change identity; `pick(keys)` keeps
 * its result's identity while every picked value is Object.is-equal, which
 * is what useSyncExternalStore needs from a getSnapshot.
 */

export type ApprovalState = 'approved' | 'rejected';
export type Approvals = Record<string, ApprovalState>;

export interface AppStoreSnapshot {
  showEvidence: boolean;
  setShowEvidence: (v: boolean) => void;
  showConfidence: boolean;
  setShowConfidence: (v: boolean) => void;
  drawer: DrawerSource | null;
  setDrawer: (d: DrawerSource | null) => void;
  lastBorrowerId: string | null;
  setLastBorrowerId: (borrowerId: string | null) => void;
  approvals: Approvals;
  setApproval: (borrowerId: string, state: ApprovalState) => void;
}

export type AppStoreKey = keyof AppStoreSnapshot;

type StateKey = 'showEvidence' | 'showConfidence' | 'drawer' | 'lastBorrowerId' | 'approvals';

export interface AppStore {
  getSnapshot: () => AppStoreSnapshot;
  subscribe: (listener: () => void) => () => void;
  pick: <K extends AppStoreKey>(keys: readonly K[]) => Pick<AppStoreSnapshot, K>;
  /** Writes one field; a functional action reads the current value, as React's setters do. */
  update: <K extends StateKey>(key: K, action: SetStateAction<AppStoreSnapshot[K]>) => void;
  /** The actor-scoped reset: approvals, drawer and last borrower; the presenter toggles stay. */
  reset: () => void;
}

function resolve<T>(action: SetStateAction<T>, current: T): T {
  return typeof action === 'function' ? (action as (prev: T) => T)(current) : action;
}

/** The approvals record with one borrower's decision set. */
export function withApproval(approvals: Approvals, borrowerId: string, state: ApprovalState): Approvals {
  return { ...approvals, [borrowerId]: state };
}

export function createAppStore(): AppStore {
  const listeners = new Set<() => void>();
  const picks = new Map<string, Partial<AppStoreSnapshot>>();

  function update<K extends StateKey>(key: K, action: SetStateAction<AppStoreSnapshot[K]>): void {
    const next = resolve(action, state[key]);
    if (Object.is(next, state[key])) return;
    state = { ...state, [key]: next };
    for (const listener of [...listeners]) listener();
  }

  let state: AppStoreSnapshot = {
    showEvidence: true,
    setShowEvidence: (v) => update('showEvidence', v),
    showConfidence: true,
    setShowConfidence: (v) => update('showConfidence', v),
    drawer: null,
    setDrawer: (d) => update('drawer', d),
    lastBorrowerId: null,
    setLastBorrowerId: (borrowerId) => {
      const next = borrowerId?.trim();
      update('lastBorrowerId', next ? next : null);
    },
    approvals: {},
    setApproval: (borrowerId, approval) => update('approvals', (cur) => withApproval(cur, borrowerId, approval)),
  };

  return {
    getSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    pick<K extends AppStoreKey>(keys: readonly K[]): Pick<AppStoreSnapshot, K> {
      const id = keys.join(',');
      const cached = picks.get(id);
      if (cached && keys.every((key) => Object.is(cached[key], state[key]))) return cached as Pick<AppStoreSnapshot, K>;
      const next = Object.fromEntries(keys.map((key) => [key, state[key]])) as Pick<AppStoreSnapshot, K>;
      picks.set(id, next);
      return next;
    },
    update,
    reset() {
      update('approvals', {});
      update('drawer', null);
      update('lastBorrowerId', null);
    },
  };
}

/** The store AppProvider owns; `useApp(...keys)` reads it. Null outside a provider. */
export const AppStoreContext = createContext<AppStore | null>(null);
