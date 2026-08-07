"""Round 32 cross-boundary governance evasions and safe-control proof."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.outreach import (
    _assert_disclosure_backed_draft_body,
    _assert_final_draft_subject,
)
from backend.schemas._validators_protected_class import contains_protected_class_marketing_text
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
from backend.schemas.borrower_cta_evidence import (
    contains_borrower_cta_contradiction,
    negative_borrower_cta_evidence,
)
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.portfolio_campaign import (
    CampaignRecommendationEvidence,
    CampaignRecommendationResponse,
    CampaignRecommendationVariant,
)
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    build_safe_audit_metadata,
)

_DISCLOSURE = SimpleNamespace(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)
_PROTECTED_COPY = (
    "Unwed homeowners can call us to review mortgage options.",
    "Churchgoing homeowners can call us to review mortgage options.",
    "Cancer assigns borrowers to the campaign. Please call us to review mortgage options.",
    "Cancer lands borrowers in the audience. Please call us to review mortgage options.",
    "Cancer slots borrowers into this campaign. Please call us to review mortgage options.",
    "Cancer is what gets borrowers this offer. Please call us to review mortgage options.",
    "This invitation follows from cancer. Please call us to review mortgage options.",
    "Expectant homeowners can call us to review mortgage options.",
    "Maternity-leave borrowers can call us to review mortgage options.",
    "Civil-union homeowners can call us to review mortgage options.",
    "Civilly partnered homeowners can call us to review mortgage options.",
    "Ramadan-observing homeowners can call us to review mortgage options.",
    "Sabbath-keeping borrowers can call us to review mortgage options.",
    "Families awaiting a newborn can call us to review mortgage options.",
    "Households raising toddlers can call us to review mortgage options.",
    "Homeowners joined in a civil partnership can call us to review mortgage options.",
    "Borrowers due to give birth can call us to review mortgage options.",
    "Civil-partner homeowners can call us to review mortgage options.",
    "Church-attending homeowners can call us to review mortgage options.",
    "Domestic-partnered homeowners can call us to review mortgage options.",
    "Homeowners who observe Ramadan can call us to review mortgage options.",
    "Homeowners expecting their first child can call us to review mortgage options.",
    "Homeowners active in their parish can call us to review mortgage options.",
    "Households welcoming a baby can call us to review mortgage options.",
    "Sunday-worshipping homeowners can call us to review mortgage options.",
    "Easter-celebrating homeowners can call us to review mortgage options.",
    "Homeowners observing Ramadan can call us to review mortgage options.",
    "Civilly wed borrowers can call us to review mortgage options.",
    "Households anticipating a child can call us to review mortgage options.",
    "Civilly united borrowers can call us to review mortgage options.",
    "Households preparing for a child can call us to review mortgage options.",
    "Sunday-service homeowners can call us to review mortgage options.",
    "Homeowners who observe Easter can call us to review mortgage options.",
    "Homeowners active in a synagogue can call us to review mortgage options.",
    "Borrowers keeping Lent can call us to review mortgage options.",
    "Homeowners who practice their faith can call us to review mortgage options.",
    "Homeowners attending mass. Call us to discuss mortgage options.",
    "Households celebrating Passover. Call us to discuss mortgage options.",
    "Homeowners active in their congregation. Call us to discuss mortgage options.",
    "Borrowers who worship on Sundays. Call us to discuss mortgage options.",
    "Homeowners awaiting childbirth should receive this offer. Contact us to review options.",
    "Homeowners awaiting a baby should receive this offer. Contact us to review mortgage options.",
    "Homeowners expecting a newborn should receive this offer. Contact us to review options.",
    "Homeowners preparing for a new arrival should receive this offer. Contact us to review options.",
    "Homeowners at Sunday mass. Please call us to discuss mortgage options.",
    "Homeowners who go to mass. Please call us to discuss mortgage options.",
    "Borrowers at worship services. Please call us to discuss mortgage options.",
    "Passover households. Please call us to discuss mortgage options.",
    "Homeowners celebrating Eid. Please call us to discuss mortgage options.",
    "Households observing Yom Kippur. Please call us to discuss mortgage options.",
    "Homeowners who celebrate Christmas should receive this offer. Contact us to review mortgage options.",
    "Homeowners headed to Sunday mass. Please call us to review mortgage options.",
    "Households expecting triplets. Please call us to review mortgage options.",
    "Households are expecting triplets. Please call us to review mortgage options.",
    "Homeowners attend Sunday mass. Please call us to review mortgage options.",
)
_IDENTITY_COPY = (
    "Your reply gets handled by zora quill; reply to discuss mortgage options.",
    "zora quill will be monitoring replies; reply to discuss mortgage options.",
    "A response routes via zora quill; reply to discuss mortgage options.",
    "zora quill processes inbound responses; reply to discuss mortgage options.",
    "Your reply is relayed through zora quill; reply to discuss mortgage options.",
    "Your reply is overseen by zora quill. Reply YES to discuss mortgage options.",
    "zora quill supervises incoming replies. Reply YES to discuss mortgage options.",
    "Your response is being answered by zora quill. Please reply to discuss mortgage options.",
    "Your response must be answered by zora quill. Please reply to discuss mortgage options.",
    "Your response is to be answered by zora quill. Please reply to discuss mortgage options.",
    "Your response comes under the supervision of zora quill. Please reply to discuss mortgage options.",
    "zora quill is due to receive this message. Please reply to discuss mortgage options.",
    "This message is being prepared by zora quill. Please reply to discuss mortgage options.",
    "This message was prepared under zora quill's supervision. Please reply to discuss mortgage options.",
    "Your response is in zora quill's care. Please reply to discuss mortgage options.",
    "zora quill takes care of your reply. Please reply to discuss mortgage options.",
    "zora quill has responsibility for replies. Please reply to discuss mortgage options.",
    "zora quill serves as the reply handler. Please reply to discuss mortgage options.",
    "Replies belong to zora quill. Please reply to discuss mortgage options.",
    "zora quill is the person who reads replies. Please reply to discuss mortgage options.",
    "Responses wind up with zora quill. Please reply to discuss mortgage options.",
    "zora quill gets your response. Please reply to discuss mortgage options.",
    "Your reply is entrusted to zora quill. Please reply to discuss mortgage options.",
    "zora quill has been tasked with reading replies. Please reply to discuss mortgage options.",
    "zora quill is set to receive this message. Please reply to discuss mortgage options.",
    "Your reply rests with zora quill. Please reply to discuss mortgage options.",
    "Your response is under zora quill's care. Please reply to discuss mortgage options.",
    "zora quill is responsible for replies. Please reply to discuss mortgage options.",
    "zora quill looks after your response. Please reply to discuss mortgage options.",
    "zora quill is on reply duty. Please reply to discuss mortgage options.",
    "zora quill is entrusted with replies. Please reply to discuss mortgage options.",
    "Responses are kept by zora quill. Please reply to discuss mortgage options.",
    "zora quill safeguards replies. Please reply to discuss mortgage options.",
    "zora quill has dominion over replies. Please reply to discuss mortgage options.",
    "zora quill fields your reply. Please reply to discuss mortgage options.",
    "The custodian for replies is zora quill. Please reply to discuss mortgage options.",
    "Please ask zora quill to review replies. Please reply to discuss mortgage options.",
    "Our replies report upward to zora quill. Please reply to discuss mortgage options.",
    "We entrust replies to zora quill. Please reply to discuss mortgage options.",
    "Replies pass through zora quill. Please reply to discuss mortgage options.",
    "A custodian for replies is zora quill. Please reply to discuss mortgage options.",
    "The custodian handling replies is zora quill. Please reply to discuss mortgage options.",
    "The reply steward is zora quill. Please reply to discuss mortgage options.",
    "Reply ownership points to zora quill. Please reply to discuss mortgage options.",
    "Your response is handled by susan team. Please reply to discuss mortgage options.",
    "Your response is handled by maria staff. Please reply to discuss mortgage options.",
    "The reply steward is hannah home. Reply to discuss mortgage options.",
    "The reply steward is zora borrower. Reply to discuss mortgage options.",
    "The response custodian is avery borrower. Reply to discuss mortgage options.",
    "The reply steward is qualified home. Reply to discuss mortgage options.",
    "The reply steward is professional borrower. Reply to discuss mortgage options.",
    "The inbox point person is zora quill. Please reply to discuss mortgage options.",
    "Reply accountability lies with zora quill. Please reply to discuss mortgage options.",
    "Reply accountability is assigned to zora quill. Please reply to discuss mortgage options.",
    "Our reply steward, zora quill, reads responses. Please reply to discuss mortgage options.",
    "The inbox liaison is zora quill. Please reply to discuss mortgage options.",
    "zora quill is our inbox point person. Please reply to discuss mortgage options.",
    "Response escalation goes to zora quill. Please reply to discuss mortgage options.",
    "We'll send what you write to zora quill. Please reply to discuss mortgage options.",
    "We'll deliver what you write to zora quill. Please reply to discuss mortgage options.",
    "We'll send whatever you write to zora quill. Please reply to discuss mortgage options.",
)
_CONTRADICTORY_CTA_COPY = (
    "You had consented to calls, then canceled it. Call us to review mortgage options.",
    "Calls lack your consent. Call us to review mortgage options.",
    "You refused phone permission. Call us to review mortgage options.",
    "Your texting consent ended. Text us to review mortgage options.",
    "You did not assent to phone contact. Call us to review mortgage options.",
    "We have not obtained authorization for calls. Call us to review mortgage options.",
    "Responses are incinerated unread. Reply to discuss mortgage options.",
    "Your reply will be trashed without review. Reply to discuss mortgage options.",
    "This mailbox bins incoming messages unread. Reply to discuss mortgage options.",
    "Our team ignores responses. Reply to discuss mortgage options.",
    "Responses are not looked at. Reply to discuss mortgage options.",
    "Your permission to text was annulled. Text us to discuss mortgage options.",
    "Replies are junked unseen. Reply YES to discuss mortgage options.",
    "Incoming replies are removed without review. Reply YES to discuss mortgage options.",
    "You've withheld consent for texts. Please text us to review mortgage options.",
    "You have yet to consent to calls. Please call us to review mortgage options.",
    "Phone permission is absent. Please call us to review mortgage options.",
    "We lack your authorization for calls. Please call us to review mortgage options.",
    "Replies are destined for deletion. Please reply to review mortgage options.",
    "Replies go straight in the trash. Please reply to review mortgage options.",
    "Replies are erased before review. Please reply to review mortgage options.",
    "Replies are fed to the shredder. Please reply to review mortgage options.",
    "Replies get shredded. Please reply to review mortgage options.",
    "All messages are obliterated. Please reply to review mortgage options.",
    "We failed to obtain consent for calls. Please call us to review mortgage options.",
    "There is zero consent for texting. Please text us to review mortgage options.",
    "You declined to opt in to calls. Please call us to review mortgage options.",
    "You've not opted in to texts. Please text us to review mortgage options.",
    "You haven't opted in to calls. Please call us to review mortgage options.",
    "You never granted consent for calls. Please call us to review mortgage options.",
    "We do not possess authorization for texts. Please text us to review mortgage options.",
    "Consent for calls is invalid. Please call us to review mortgage options.",
    "Authorization for texts is void. Please text us to review mortgage options.",
    "We never secured consent for calls. Please call us to review mortgage options.",
    "Replies are reduced to ashes. Please reply to review mortgage options.",
    "Replies are pulverized. Please reply to review mortgage options.",
    "Replies are wiped. Please reply to review mortgage options.",
    "Replies are sent to oblivion. Please reply to review mortgage options.",
    "Replies have no chance of being read. Please reply to review mortgage options.",
    "Responses die in the queue. Please reply to review mortgage options.",
    "Replies are tossed unread. Please reply to review mortgage options.",
    "Replies are scrapped. Please reply to review mortgage options.",
    "Replies are ground to dust. Please reply to review mortgage options.",
    "Replies are tossed away unread. Please reply to review mortgage options.",
    "We came up short of obtaining consent for calls. Please call us to review mortgage options.",
    "You chose against opting in to texts. Please text us to review mortgage options.",
    "We are missing permission for calls. Please call us to review mortgage options.",
    "Your consent for calls is nonexistent. Please call us to review mortgage options.",
    "Calls are unauthorized. Please call us to review mortgage options.",
    "Responses rot in the queue. Please reply to discuss mortgage options.",
    "Nobody notices replies. Please reply to discuss mortgage options.",
    "Replies are vaporized. Please reply to discuss mortgage options.",
    "Replies are crushed. Please reply to discuss mortgage options.",
    "We lack the go-ahead to call. Please call us to review mortgage options.",
    "Calling is off limits. Please call us to review mortgage options.",
    "You never said yes to calls. Please call us to review mortgage options.",
    "The inbox vaporizes every reply. Please reply to discuss mortgage options.",
    "Our queue lets responses rot. Please reply to discuss mortgage options.",
    "Anything you send gets crushed. Please reply to discuss mortgage options.",
    "Nothing sent here is ever seen. Please reply to discuss mortgage options.",
    "The queue swallows every message. Please reply to discuss mortgage options.",
    "Whatever you send vanishes. Please reply to discuss mortgage options.",
    "You didn't approve calls. Please call us to review mortgage options.",
    "We don't have clearance to text. Please text us to review mortgage options.",
    "You never gave the okay to call. Please call us to review mortgage options.",
    "Our system swallows every reply. Please reply to discuss mortgage options.",
    "All you send disappears. Please reply to discuss mortgage options.",
    "Any message you send gets destroyed. Please reply to discuss mortgage options.",
    "We have no leave to call. Please call us to review mortgage options.",
    "The homeowner never agreed to phone outreach. Call us to review mortgage options.",
    "The customer would not allow texts. Text us to review mortgage options.",
    "The borrower withheld acquiescence for calls. Call us to review mortgage options.",
    "Consent for calls is deficient. Please call us to review mortgage options.",
    "Permission for calls is insufficient. Please call us to review mortgage options.",
    "Your call consent is stale. Please call us to review mortgage options.",
    "The retention engine devours every reply. Reply YES to review mortgage options.",
    "The automation consumes responses. Reply YES to review mortgage options.",
    "Your reply sinks without a trace. Please reply to discuss mortgage options.",
    "What you send is buried forever. Please reply to discuss mortgage options.",
    "This address has no human monitor. Please reply to discuss mortgage options.",
    "We abandon anything that arrives here. Please reply to discuss mortgage options.",
    "The homeowner forbade telephone outreach. Please call us to review mortgage options.",
    "The homeowner objects to phone contact. Call us to discuss mortgage options.",
    "We do not have the homeowner's blessing to phone them. Call us to discuss mortgage options.",
    "Assent for phone contact is absent. Call us to discuss mortgage options.",
    "The customer objected to receiving texts. Text us to discuss mortgage options.",
    "The homeowner disavowed our right to call. Call us to discuss mortgage options.",
    "Inbound correspondence is discarded unread. Please reply to discuss mortgage options.",
    "The response repository inters every submission. Please reply to discuss mortgage options.",
    "Anything arriving here falls through the cracks. Reply to discuss mortgage options.",
    "The homeowner vetoed telephone outreach. Please call us to review mortgage options.",
    "The homeowner instructed us against calling. Please call us to review mortgage options.",
    "The customer nixed phone contact. Please call us to review mortgage options.",
    "The homeowner forbade us to telephone them. Please call us to discuss mortgage options.",
    "The homeowner withheld their blessing for phone contact. Please call us to discuss mortgage options.",
    "We have no authority to telephone the homeowner. Please call us to discuss mortgage options.",
    "The homeowner is opposed to phone contact. Please call us to discuss mortgage options.",
    "The homeowner is unwilling to receive calls. Please call us to discuss mortgage options.",
    "The homeowner prohibited phone contact. Please call us to review mortgage options.",
    "The borrower has not permitted calls. Please call us to review mortgage options.",
    "The homeowner barred our calls. Call us to review mortgage options.",
    "Our intake ledger buries every response unexamined. Please reply to discuss mortgage options.",
    "The response vault sequesters submissions beyond staff access. Please reply to discuss mortgage options.",
    "Inbound correspondence gets interred unread. Please reply to discuss mortgage options.",
    "What arrives here is never read. Please reply to discuss mortgage options.",
    "Anything arriving here disappears forever. Please reply to discuss mortgage options.",
    "Any inbound correspondence gets buried unread. Please reply to discuss mortgage options.",
    "Correspondence winds up unread. Please reply to discuss mortgage options.",
    "The response archive inters every submission. Please reply to discuss mortgage options.",
    "Your reply shall be forgotten. Please reply to discuss mortgage options.",
    "Your reply ought to be forgotten. Please reply to discuss mortgage options.",
)
_SAFE_STAFFED_RESPONSE_COPY = (
    "Your response is handled by experienced support staff. Please reply to discuss mortgage options.",
    "Your response is answered by trained mortgage specialists. Please reply to discuss mortgage options.",
    "The reply is monitored by dedicated support staff. Please reply to discuss mortgage options.",
    "A response routes via secure servicing workflows. Please reply to discuss mortgage options.",
    "Your response is handled by knowledgeable customer service representatives. Please reply to discuss mortgage options.",
    "Your response is handled by courteous customer care representatives. Please reply to discuss mortgage options.",
    "Your response is handled by professional borrower support representatives. Please reply to discuss mortgage options.",
    "Your response is handled by qualified home lending associates. Please reply to discuss mortgage options.",
    "Your response is handled by responsive customer support staff. Please reply to discuss mortgage options.",
    "Your response is handled by friendly lending professionals. Please reply to discuss mortgage options.",
    "We verify consent before calls. Please call us to discuss mortgage options.",
    "Replies are stored securely. Please reply to discuss mortgage options.",
    "Responses enter our secure queue. Please reply to discuss mortgage options.",
    "Consent records for calls are reviewed monthly. Please call us to discuss mortgage options.",
    "Call consent is documented in our compliance system. Please call us to discuss mortgage options.",
    "Consent for calls follows our privacy policy. Please call us to discuss mortgage options.",
    "Replies remain available for follow-up. Please reply to discuss mortgage options.",
    "Responses receive confirmation. Please reply to discuss mortgage options.",
    "Whatever you send is stored securely. Please reply to discuss mortgage options.",
    "Our system encrypts replies. Please reply to discuss mortgage options.",
    "All you send is stored securely. Please reply to discuss mortgage options.",
    "Any message you send is stored securely. Please reply to discuss mortgage options.",
    "Responses remain in a monitored queue until review. Please reply to discuss mortgage options.",
    "Replies are encrypted at rest. Please reply to discuss mortgage options.",
    "We confirm consent before telephone outreach. Please call us to discuss mortgage options.",
    "Our compliance team validates call consent before contact. Please call us to discuss mortgage options.",
    "The inbox liaison team reviews every reply. Please reply to discuss mortgage options.",
    "The privacy team audits phone authorization before outreach. Please call us to discuss mortgage options.",
    "Our team audits phone consent before phone outreach. Please call us to discuss mortgage options.",
    "We check email authorization before email outreach. Please email us to discuss mortgage options.",
    "The team reviews text consent records monthly. Please text us to discuss mortgage options.",
    "Our compliance department validates telephone outreach consent before contact. Call us.",
    "We verify the homeowner's consent before telephone outreach. Please call us to discuss mortgage options.",
    "Incoming correspondence is reviewed by our compliance team. Please reply to discuss mortgage options.",
    "Your reply shall be reviewed by our compliance team. Please reply to discuss mortgage options.",
    "Your response is handled by trained professional mortgage lending specialists. Please reply to discuss mortgage options.",
    "A mortgage specialist can explain lending standards. Contact us to review mortgage options.",
    "A loan professional can review lending standards with you. Contact us to review mortgage options.",
    "Medical history is excluded from targeting and never influences eligibility. Contact us to review mortgage options.",
    "Clinical data is excluded from campaign targeting. Contact us to review mortgage options.",
    "Cancer data is excluded from selection, and borrowers are selected based on current LTV. Contact us.",
)

_SAFE_CONTEXTUAL_STATUS_COPY = (
    "Rate-observing borrowers can call us to review mortgage options.",
    "Deadline-observant applicants can call us to review mortgage options.",
)

_COORDINATED_HEALTH_COPY = (
    "Cancer assigns borrowers to the campaign and health data is excluded from selection. Please call us.",
    "Cancer lands borrowers in the audience and medical data is never used for eligibility. Please call us.",
    "This invitation follows from cancer and health data is excluded from selection. Please call us.",
    "Cancer eligibility is not based on health but cancer gets borrowers this offer. Call us.",
    "Cancer eligibility does not depend on health but cancer lands borrowers in this audience. Call us.",
    "Cancer selection does not depend on health but cancer funnels homeowners into this campaign. Call us.",
    "Cancer eligibility does not depend on health, but cancer assigns borrowers to this campaign. Call us.",
    "Diabetes is never used for eligibility, but diabetic borrowers enter this offer. Call us.",
    "Cancer is never used in targeting, while cancer determines who joins this campaign. Call us.",
    "Cancer information funnels homeowners into this offer and is not used for eligibility. Call us.",
    "Diabetes records land borrowers in this audience and are retained only for compliance review. Call us.",
    "Cancer data brings borrowers into this offer and is excluded from selection. Call us.",
    "Cancer data assigns borrowers to the campaign and is excluded from targeting. Call us.",
    "Cancer information determines who joins this campaign and is not used for targeting. Call us.",
    "Cancer documentation assigns borrowers to this campaign and is excluded from selection. Please call us.",
    "Cancer data is excluded from selection and assigns borrowers to this campaign. Call us.",
    "Diabetes records are retained only for compliance review yet land borrowers in this audience. Call us.",
    "Cancer information is not used for eligibility and determines who joins this campaign. Call us.",
    "Document aggregate cancer patient campaign statistics for governance review. Please call us.",
    "Document aggregate cancer borrower selection statistics for governance review. Please call us.",
    "Cancer data is excluded from targeting; it still selects borrowers. Call us.",
    "Cancer data is excluded from selection; it nevertheless assigns borrowers to this campaign. Call us.",
    "Cancer data is excluded from targeting. It still selects borrowers. Call us.",
    "Cancer and diabetes information is excluded from targeting. They nevertheless assign borrowers. Call us.",
    "Cancer data is excluded from selection. Those records move homeowners to the front of the line. Call us.",
    "Diabetes records are retained only for compliance review. Those records make some borrowers a priority. Call us.",
    "Cancer information is excluded from eligibility. Even so, the records decide which homeowners hear from us. Call us.",
    "Cancer data is excluded from selection. Even so, the clinical charts move homeowners ahead. Call us.",
    "Medical history is excluded from targeting. Still, case notes decide who hears from us. Call us.",
    "Cancer data is excluded from selection. Despite this, those records rank borrowers. Contact us.",
    "Clinical data is excluded from campaign targeting; the same data determines who joins the audience. Contact us.",
    "Clinical data is excluded from campaign targeting; identical data determines who joins the audience. Contact us.",
    "Cancer data is excluded from selection. In spite of this, those records rank borrowers. Contact us.",
    "Cancer data is excluded from selection. However, it selects borrowers. Call us.",
    "Cancer data is excluded from selection. Nevertheless, those records prioritize borrowers. Call us.",
    "Cancer data is excluded from selection. Even then, those records move homeowners to the front. Call us.",
    "Cancer data is excluded from selection. Those files put homeowners first. Call us.",
    "Cancer data is excluded from selection. The records dictate who hears from us. Call us.",
    "Cancer data is excluded from selection. Such records are used to prioritize borrowers. Call us.",
)

_UNSUPPORTED_CLAIM_COPY = (
    "You qualify for our support team mortgage offer. Contact us to review mortgage options.",
    "This offer is available because you meet our lending requirements. Contact us to review mortgage options.",
    "You meet our eligibility criteria for this mortgage offer. Contact us to discuss mortgage options.",
    "Our records show you meet the requirements for this mortgage offer. Contact us to discuss mortgage options.",
    "Our records show that you meet the requirements for this mortgage offer. Contact us to discuss mortgage options.",
    "We determined that this mortgage is suitable for you. Contact us to review mortgage options.",
    "Your profile satisfies our lending standards. Contact us to review mortgage options.",
    "You clear our underwriting bar. Contact us to review mortgage options.",
    "You pass our eligibility screen. Contact us to review mortgage options.",
    "Based on our records, you fit the requirements. Contact us to review mortgage options.",
    "You have met our lending requirements. Contact us to discuss mortgage options.",
    "You seem eligible for this mortgage offer. Contact us to discuss mortgage options.",
    "Our review confirms your eligibility. Contact us to discuss mortgage options.",
    "You passed our eligibility review. Contact us to discuss mortgage options.",
    "We found you eligible for this mortgage offer. Contact us to discuss mortgage options.",
    "We have approved you for this offer. Contact us to discuss mortgage options.",
    "We've approved you for this offer. Contact us to discuss mortgage options.",
    "We approved your application for this offer. Contact us to discuss mortgage options.",
    "Our review verifies your eligibility. Contact us to discuss mortgage options.",
    "Our assessment proves your eligibility. Contact us to discuss mortgage options.",
    "Your application has satisfied every eligibility condition. Contact us to review mortgage options.",
)

_SAFE_HEALTH_COPY = (
    "Cancer documentation is excluded from campaign targeting. Please call us to review mortgage options.",
    "Diabetes information is retained only for compliance review. Please call us to review mortgage options.",
    "Eligibility does not depend on cancer. Please call us to review mortgage options.",
    "Cancer is never required for eligibility. Please call us to review mortgage options.",
    "Cancer and diabetes information is excluded from campaign targeting.",
)


def _variant(*, body: str, subject: str = "Mortgage options") -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


def _assert_shared_schema_and_delivery_rejection(copy: str, *, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _variant(body=copy)
    with pytest.raises(ValidationError, match=reason):
        _variant(body="Contact us to review mortgage options.", subject=copy)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        GrowthAgentPromptRunRequest(prompt=copy)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        ComposePlanRequest(objective=copy)
    with pytest.raises(HTTPException, match=reason):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{copy} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match=reason):
        _assert_final_draft_subject(draft_subject=copy, channel="email")


def _assert_all_audit_text_rejected(copy: str, *, reason: str) -> None:
    for field in ("draft_body", "draft_subject", "reason", "notes"):
        with pytest.raises(AuditMetadataValueViolation, match=reason):
            build_safe_audit_metadata({field: copy}, action="outreach.approve")


@pytest.mark.parametrize("copy", _PROTECTED_COPY)
def test_protected_status_aliases_reject_every_creation_and_delivery_boundary(copy: str) -> None:
    _assert_shared_schema_and_delivery_rejection(copy, reason="protected-class")
    _assert_all_audit_text_rejected(copy, reason="protected-class")


@pytest.mark.parametrize("copy", _COORDINATED_HEALTH_COPY)
def test_health_safe_assertions_cannot_mask_unsafe_selection(copy: str) -> None:
    _assert_shared_schema_and_delivery_rejection(copy, reason="protected-class")
    _assert_all_audit_text_rejected(copy, reason="protected-class")


@pytest.mark.parametrize("copy", _IDENTITY_COPY)
def test_responder_identity_relationships_reject_every_boundary(copy: str) -> None:
    assert contains_borrower_copy_contextual_name(copy)
    _assert_shared_schema_and_delivery_rejection(copy, reason="human-name-shaped")
    _assert_all_audit_text_rejected(copy, reason="human-name-shaped")


@pytest.mark.parametrize("copy", _CONTRADICTORY_CTA_COPY)
def test_consent_and_dead_response_relationships_reject_every_boundary(copy: str) -> None:
    assert negative_borrower_cta_evidence(copy)
    assert contains_borrower_cta_contradiction(copy)
    _assert_shared_schema_and_delivery_rejection(copy, reason="call to action")
    _assert_all_audit_text_rejected(copy, reason="contradicts consent")


@pytest.mark.parametrize("copy", _UNSUPPORTED_CLAIM_COPY)
def test_direct_qualification_claims_reject_every_boundary(copy: str) -> None:
    _assert_shared_schema_and_delivery_rejection(copy, reason="unsupported borrower-facing")
    _assert_all_audit_text_rejected(copy, reason="unsupported borrower-facing")


@pytest.mark.parametrize(
    "copy",
    (
        "Cancer assigns borrowers to the campaign.",
        "Cancer data assigns borrowers and is excluded from selection.",
        "Cancer data is excluded from selection and assigns borrowers to this campaign.",
        "Diabetes data is retained only for compliance review yet lands borrowers.",
        "Cancer data is not used for eligibility and selects borrowers.",
        "Cancer data is excluded; it still selects borrowers.",
        "Document aggregate cancer campaign statistics for governance review.",
    ),
)
def test_protected_selection_rejects_every_campaign_recommendation_text_field(
    copy: str,
) -> None:
    safe_variants = [
        CampaignRecommendationVariant(
            variant_name=name,
            subject="Mortgage options",
            body="Contact us to review mortgage options.",
            hypothesis="A reviewed invitation may support a response.",
        )
        for name in ("Benefit-led", "Guidance-led")
    ]
    safe_evidence = CampaignRecommendationEvidence(
        label="Reviewed signal",
        value="Current mortgage opportunity",
        source_asset="mip.gold.borrower_360",
    )

    for variant_field in ("subject", "body", "hypothesis"):
        payload = {
            "variant_name": "Guidance-led",
            "subject": "Mortgage options",
            "body": "Contact us to review mortgage options.",
            "hypothesis": "A reviewed invitation may support a response.",
            variant_field: copy,
        }
        with pytest.raises(ValidationError, match="protected-class"):
            CampaignRecommendationVariant(**payload)
    for evidence_field in ("label", "value"):
        payload = {
            "label": "Reviewed signal",
            "value": "Current mortgage opportunity",
            "source_asset": "mip.gold.borrower_360",
            evidence_field: copy,
        }
        with pytest.raises(ValidationError, match="protected-class"):
            CampaignRecommendationEvidence(**payload)
    for response_field in ("generator_label", "audience_summary", "strategy"):
        payload = {
            "generation_mode": "reviewed_fallback",
            "generator_label": "Mortgage Growth Agent",
            "performance_status": "qualified",
            "audience_summary": "Reviewed mortgage audience",
            "strategy": "Provide reviewed mortgage options.",
            "variants": safe_variants,
            "holdout_pct": 10,
            "evidence": [safe_evidence],
            "warnings": [],
            response_field: copy,
        }
        with pytest.raises(ValidationError, match="protected-class"):
            CampaignRecommendationResponse(**payload)
    with pytest.raises(ValidationError, match="protected-class"):
        CampaignRecommendationResponse(
            generation_mode="reviewed_fallback",
            generator_label="Mortgage Growth Agent",
            performance_status="qualified",
            audience_summary="Reviewed mortgage audience",
            strategy="Provide reviewed mortgage options.",
            variants=safe_variants,
            holdout_pct=10,
            evidence=[safe_evidence],
            warnings=[copy],
        )


@pytest.mark.parametrize("field", ("rationale", "bulk_rationale", "reason", "notes"))
@pytest.mark.parametrize(
    ("copy", "reason"),
    (
        ("Target unwed homeowners for this offer.", "protected-class"),
        ("Prioritize borrowers because they are Muslim.", "protected-class"),
        ("Call zora quill about this borrower.", "human-name-shaped"),
    ),
)
def test_audit_free_text_rejects_governance_content_without_request_schema(
    field: str,
    copy: str,
    reason: str,
) -> None:
    with pytest.raises(AuditMetadataValueViolation, match=reason):
        build_safe_audit_metadata({field: copy}, action="outreach.approve")


@pytest.mark.parametrize(
    "copy",
    (
        "Replies won't be read automatically, but staff will read them. Reply YES.",
        *_SAFE_STAFFED_RESPONSE_COPY,
    ),
)
def test_reviewed_safe_copy_passes_every_boundary(copy: str) -> None:
    assert negative_borrower_cta_evidence(copy) == []
    assert not contains_borrower_cta_contradiction(copy)
    assert _variant(body=copy).body == copy
    assert GrowthAgentPromptRunRequest(prompt=copy).prompt == copy
    assert ComposePlanRequest(objective=copy).objective == copy
    body = f"{copy} {_DISCLOSURE.body}"
    assert (
        _assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=_DISCLOSURE,
            channel="email",
        )
        == body
    )
    assert _assert_final_draft_subject(draft_subject=copy, channel="email") == copy
    for field in ("draft_body", "draft_subject", "reason", "notes"):
        assert build_safe_audit_metadata({field: copy}, action="outreach.approve")[field] == copy


@pytest.mark.parametrize("copy", _SAFE_CONTEXTUAL_STATUS_COPY)
def test_business_observance_language_is_not_misclassified_as_protected(copy: str) -> None:
    assert not contains_protected_class_marketing_text(copy)
    assert _variant(body=copy).body == copy


@pytest.mark.parametrize("copy", _SAFE_HEALTH_COPY)
def test_reviewed_health_exclusion_is_not_misclassified_as_protected_targeting(copy: str) -> None:
    assert not contains_protected_class_marketing_text(copy)
