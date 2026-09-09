/**
 * Workspace endpoint clients: the per-session saved lead and saved draft
 * state that backs the Console right rail.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type {
  SessionResponse,
  SavedDraft,
  SavedDraftInput,
  SavedLead,
  SavedLeadInput,
  WorkspaceMutationResult,
  WorkspaceState,
} from '../../types';
import { getJson, putJson, deleteJson } from '../apiTransport';

export const workspaceApi = {
  workspace: (signal?: AbortSignal) =>
    getJson<WorkspaceState>('/api/workspace', signal),

  session: (signal?: AbortSignal) =>
    getJson<SessionResponse>('/api/session', signal),

  saveWorkspaceLead: (lead: SavedLeadInput, signal?: AbortSignal) =>
    putJson<SavedLead, SavedLeadInput>(
      `/api/workspace/leads/${encodeURIComponent(lead.borrower_id)}`,
      lead,
      signal,
    ),

  deleteWorkspaceLead: (borrowerId: string, signal?: AbortSignal) =>
    deleteJson<WorkspaceMutationResult>(
      `/api/workspace/leads/${encodeURIComponent(borrowerId)}`,
      signal,
    ),

  saveWorkspaceDraft: (draft: SavedDraftInput, signal?: AbortSignal) =>
    putJson<SavedDraft, SavedDraftInput>(
      `/api/workspace/drafts/${encodeURIComponent(draft.borrower_id)}`,
      draft,
      signal,
    ),

  deleteWorkspaceDraft: (
    borrowerId: string,
    channel: 'email' | 'sms' | 'direct_mail' = 'email',
    signal?: AbortSignal,
  ) =>
    deleteJson<WorkspaceMutationResult>(
      `/api/workspace/drafts/${encodeURIComponent(borrowerId)}?channel=${encodeURIComponent(channel)}`,
      signal,
    ),
};
