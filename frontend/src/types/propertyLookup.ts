import type { SegmentCode } from '../types';

/**
 * Contracts for the governed property loan lookup
 * (`POST /api/v1/lookup/property-loan`).
 *
 * Boundary honesty: this is a SHARE-SCOPED, EXACT-after-canonicalization
 * lookup against the refreshed Cotality share — NOT Cotality's CLIP
 * mastering / resolution service. The response NEVER echoes the submitted
 * `address_line`; every surfaced identifier is masked or already-public.
 * The UI must not persist the address either (component state only — no
 * localStorage, no query params).
 */

export interface PropertyLoanLookupRequest {
  /** Street address the caller typed (4–200 chars). Never persisted/echoed. */
  address_line: string;
  /** 5 ASCII digits. */
  zip5: string;
  city?: string | null;
  state?: string | null;
}

export interface PropertyLoanLookupLoan {
  lender_brand?: string | null;
  current_rate: number;
  current_lien_balance: number;
  has_open_lien: boolean;
  ltv: number;
}

export interface PropertyLoanLookupResponse {
  matched: boolean;
  match_basis: 'exact_normalized_address_zip';
  clip_ref?: string | null;
  owner_link_ref?: string | null;
  borrower_id?: string | null;
  lead_score?: number | null;
  segment?: SegmentCode[] | null;
  loan?: PropertyLoanLookupLoan | null;
  dossier_path?: string | null;
  audit_event_id: string;
}
