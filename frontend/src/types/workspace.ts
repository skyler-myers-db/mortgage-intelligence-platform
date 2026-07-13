export interface SavedLead {
  borrower_id: string;
  city?: string | null;
  state?: string | null;
  zip?: string | null;
  recommended_offer?: string | null;
  opportunity_score?: number | null;
  confidence?: number | null;
  saved_at: string;
  updated_at: string;
}

export type SavedLeadInput = Omit<SavedLead, 'saved_at' | 'updated_at'>;

export interface SavedDraft {
  borrower_id: string;
  offer_code?: string | null;
  channel: 'email' | 'sms' | 'direct_mail';
  subject?: string | null;
  body: string;
  saved_at: string;
  updated_at: string;
}

export type SavedDraftInput = Omit<SavedDraft, 'saved_at' | 'updated_at'>;

export interface WorkspaceState {
  saved_leads: SavedLead[];
  saved_drafts: SavedDraft[];
}

export interface SessionResponse {
  can_access_admin: boolean;
}

export interface WorkspaceMutationResult {
  ok: boolean;
  borrower_id: string;
  audit_event_id?: string | null;
}
