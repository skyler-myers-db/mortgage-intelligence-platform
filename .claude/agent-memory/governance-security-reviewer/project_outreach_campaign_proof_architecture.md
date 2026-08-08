---
name: outreach-campaign-proof-architecture
description: Verified governance-proof architecture for outreach/campaign approval + HMAC provenance; guards against recurring false-positive review findings
metadata:
  type: project
---

Verified @fce0816 (release governance review). Two non-obvious governance facts that reviewers keep re-flagging as false-positive blockers:

1. **HMAC token signing pattern is correct, not "unsigned payload."** `genie_actions._sign_action_claims` and `campaign_intelligence._encode_provenance_claims` sign the base64 body that *contains* all binding fields (copy_hash, criteria_fingerprint, borrower_ids, actor, exp, nonce). Verification recomputes the MAC with `hmac.compare_digest` and, for campaigns, re-derives `copy_hash` from the *stored variant* and `criteria_fingerprint` from the *caller* (campaign_intelligence.py ~433-435). Forging a modified token requires the secret. Do NOT flag "payload not in signature" — the payload IS the signed body.
   **Why:** a prior review round raised this as a BLOCKER; it is standard HMAC and was refuted by code.
   **How to apply:** when a finding claims a provenance/confirmation token is forgeable by editing claims, check whether modifying claims invalidates the MAC (it does) before rating it.

2. **`generated_outreach_drafts` need not be append-only.** The authoritative, immutable human-approval evidence is `mip_app.action_audit` (append-only trigger + REVOKE UPDATE/DELETE FROM PUBLIC). The approve `decision_intent` canonicalizes the final `draft_body`/`draft_subject`/`draft_generation_id`/`draft_response_hash`; `decision_payload_hash` of that intent lands in the immutable ledger (outreach.py ~963-984, 1060). So exact approved content + hash are tamper-evident regardless of the mutable proof table.
   **Why:** PII/audit reviewer rated the missing trigger a BLOCKER; downgraded to low because approval audit already binds the content hash immutably.
   **How to apply:** treat missing append-only on secondary proof tables as low hardening if the approval `decision_intent` already hashes the same content into `action_audit`.

3. **`generator_label`/`generation_mode` are server-constrained, not user/model free text.** databricks_portfolio.py ~806-826 hard-codes label to "Operator edited" for operator mode, else takes label/mode from the HMAC-signed provenance proof and rejects any mismatch. No PII path into audit via these fields. See [[address-lookup-governance]] for the parallel fail-closed HMAC pattern.
