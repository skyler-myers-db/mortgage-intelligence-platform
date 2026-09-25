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
  generation_id: string;
  response_hash: string;
  offer_code?: string | null;
  channel: 'email' | 'sms' | 'direct_mail';
  subject?: string | null;
  body: string;
  saved_at: string;
  updated_at: string;
}

export type SavedDraftInput = Pick<
  SavedDraft,
  'borrower_id' | 'generation_id' | 'response_hash'
>;

export interface WorkspaceState {
  saved_leads: SavedLead[];
  saved_drafts: SavedDraft[];
}

export interface SessionResponse {
  can_access_admin: boolean;
  /** Same fail-closed decision `require_approver` enforces server-side. */
  can_approve: boolean;
  /**
   * The caller's own forwarded identity — the name an approval's audit row
   * is recorded under. Null when the edge forwarded none. Display only:
   * never send it to telemetry.
   */
  actor_email?: string | null;
  /**
   * Readable label derived from the same forwarded identity (no directory
   * lookup): "jane.doe@..." reads "Jane Doe". Null exactly when actor_email
   * is. Display only: never send it to telemetry.
   */
  actor_display_name?: string | null;
  /**
   * Capability tiers as display labels, most privileged first
   * ("Administrator", "Approver", else "Workspace user"). The can_* flags
   * stay the authorization contract; these are for the identity menu.
   */
  role_labels?: string[];
  /**
   * The configured lender's display name, the same settings value
   * /config/options returns (audit delivery-07): the tenant label reads it
   * from this zero-dependency call first. Absent from an older backend.
   */
  lender_name?: string | null;
  /** The opt-in RUM gate, the same settings value /config/options returns. */
  rum_enabled?: boolean | null;
}

export interface WorkspaceMutationResult {
  ok: boolean;
  borrower_id: string;
  audit_event_id?: string | null;
}
