/**
 * The Console workspace's writes (saved leads, saved drafts) on the TanStack
 * mutation layer, and their failure copy (2026-09-21 audit stack-09 item 3,
 * states-04, runtime-03).
 *
 * AppContext is in the initial chunk, so it does not take useMutation /
 * MutationObserver (lazy-only vendor modules, tools/check_frontend_budgets.mjs
 * LAZY_ONLY_VENDOR_MODULES): `runWorkspaceWrite` builds the mutation on the
 * client's MutationCache and executes it, the engine useMutation drives,
 * without the observer. So a write is visible to `isMutating` and the mutation
 * cache, and writes to ONE target share a scope and run in order (a remove
 * never races its save).
 *
 * Posture (pinned by workspace.test.ts):
 *   - optimistic with rollback in AppContext: a workspace save is a personal
 *     bookmark, not an approval (approvals stay pessimistic elsewhere);
 *   - networkMode 'always' and retry false (the client defaults): a write
 *     started offline fails at once, as the direct call did;
 *   - NO client request_id: the workspace API has none (SavedDraftInput is
 *     extra='forbid'), and a request_id would switch the transport to its
 *     'default' policy and re-send a PUT whose handler already ran. Without
 *     it a 503 is never re-sent ('rejected-only').
 *
 * Failure copy never interpolates an error's message: a fixed sentence per
 * action, plus the binding's own sentence for a 409 actor_changed (shared
 * with describeApiError through lib/apiActorBinding). The lazy
 * describeApiError refinement of every other cause is W5d cut 2 (it split
 * the transport into its own initial chunk: +0.41 KiB br), moved to W5e
 * w5-lexicon-copy.
 */
import type { QueryClient } from '@tanstack/react-query';
import { ACTOR_CHANGED_COPY, ACTOR_CHANGED_REASON } from '../apiActorBinding';
import { ApiError } from '../apiTransport';

export type WorkspaceWriteKind = 'lead' | 'draft';
export type WorkspaceWriteOp = 'save' | 'remove';
export type WorkspaceAction = 'load' | 'saveLead' | 'removeLead' | 'saveDraft' | 'removeDraft';

/** The fixed per-action copy; the only text set synchronously. */
export const WORKSPACE_FAILURE: Readonly<Record<WorkspaceAction, string>> = {
  load: "Couldn't load the saved workspace.",
  saveLead: "Couldn't save the lead.",
  removeLead: "Couldn't remove the saved lead.",
  saveDraft: "Couldn't save the draft.",
  removeDraft: "Couldn't remove the draft.",
};

export const WORKSPACE_WRITE_KEY = ['mip', 'workspace', 'write'] as const;

export interface WorkspaceWrite<T> {
  kind: WorkspaceWriteKind;
  op: WorkspaceWriteOp;
  /** The borrower (and channel, for a draft) the write touches: the scope. */
  target: string;
  write: () => Promise<T>;
}

/** Run one workspace write as a mutation on the client's cache, serialised per target. */
export function runWorkspaceWrite<T>(queryClient: QueryClient, { kind, op, target, write }: WorkspaceWrite<T>): Promise<T> {
  return queryClient
    .getMutationCache()
    .build<T, unknown, void, unknown>(queryClient, {
      mutationKey: [...WORKSPACE_WRITE_KEY, kind, op, target],
      mutationFn: write,
      networkMode: 'always',
      scope: { id: `workspace:${kind}:${target}` },
    })
    .execute(undefined);
}

/** `record` with `key` set to `value` (a computed key, kept out of the compiled component). */
export function withEntry<T>(record: Record<string, T>, key: string, value: T): Record<string, T> {
  return { ...record, [key]: value };
}

/** `record` without `key`. */
export function withoutKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const { [key]: _removed, ...rest } = record;
  return rest;
}

type ErrorSetter = (update: (current: string | null) => string | null) => void;

/**
 * Show the fixed copy for `action`; a refused request bound to the previous
 * user adds the binding's sentence. Never the error's own message.
 */
export function reportWorkspaceFailure(action: WorkspaceAction, error: unknown, setError: ErrorSetter): void {
  const fixed = WORKSPACE_FAILURE[action];
  const changed = error instanceof ApiError && error.reason === ACTOR_CHANGED_REASON;
  setError(() => (changed ? `${fixed} ${ACTOR_CHANGED_COPY}` : fixed));
}
