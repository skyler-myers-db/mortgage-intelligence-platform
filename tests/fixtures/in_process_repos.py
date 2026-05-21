"""In-process synthetic repositories -- TEST FIXTURE ONLY.

Moved in Slice 4 from ``backend/services/repositories/in_process.py``
to ``tests/fixtures/in_process_repos.py`` so nothing under
``backend/`` can accidentally import a mock-backed repo. Tests inject
these via FastAPI ``dependency_overrides`` in ``tests/conftest.py``;
production code paths are served by the ``Databricks*Repository``
classes in ``backend.services.repositories.databricks_repo``.

Each class below implements one Protocol from
``backend.services.repositories.protocols`` and delegates to the
synthetic population in ``tests/fixtures/mock_population.py``.
"""
from __future__ import annotations

from backend.schemas.analytics import (
    AnalyticsFilters,
    AnalyticsScope,
    EconomicsAnalyticsResponse,
    EquitySpreadPoint,
    EvidenceBySignalRow,
    EvidenceDailyRow,
    ExecutiveAnalyticsResponse,
    FunnelStage,
    FunnelTotals,
    GeographyAnalyticsResponse,
    RateSpreadBucket,
    ScoreBucket,
    SegmentAnalyticsResponse,
    SegmentByStateRow,
    SegmentMetricRow,
    SegmentOverviewRow,
    SignalAnalyticsResponse,
    SignalEvidenceExample,
    StateAvmValueRow,
    StateOpportunityRow,
    TopBorrowerAnalyticsRow,
    TopSegmentByStateRow,
    TopZipOpportunityRow,
)
from backend.schemas.common import EvidenceEvent
from backend.schemas.geo import (
    CountyRollup,
    CountyRollupResponse,
    StateRollup,
    StateRollupResponse,
    ZipRollup,
    ZipRollupResponse,
)
from backend.schemas.lead import Borrower360, LeadSummary, SegmentSummary
from backend.schemas.portfolio import (
    CampaignListResponse,
    CampaignStatusPatchRequest,
    CampaignSummary,
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioCriteria,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.services.genie_answers import (
    GenieActionSuggestion,
    GenieMessageResponse,
    GenieProof,
    GenieVisualizationSpec,
    default_follow_up_questions,
)
from tests.fixtures import mock_population as mock_data


class InProcessMockPortfolioRepository:
    """Test fixture implementing ``PortfolioRepository`` from the synthetic population."""

    def preview(self, request: PortfolioPreviewRequest | None) -> PortfolioPreview:
        _ = request  # criteria don't shift the preview numbers yet; deterministic payload.
        return mock_data.PORTFOLIO

    def create(
        self,
        payload: PortfolioCreateRequest,
        *,
        actor: str | None = None,
    ) -> PortfolioCreateResponse:
        _ = actor
        return PortfolioCreateResponse(
            portfolio_id="11111111-1111-4111-8111-111111111111",
            campaign_id="11111111-1111-4111-8111-111111111111",
            name=payload.name,
            marketable_population=mock_data.PORTFOLIO.marketable_population,
        )

    def get(self, portfolio_id: str) -> dict[str, object]:
        return self._campaign(portfolio_id).model_dump()

    def _campaign(self, campaign_id: str) -> CampaignSummary:
        return CampaignSummary(
            campaign_id=campaign_id,
            name="Synthetic campaign",
            owner_email="skyler@entrada.ai",
            status="draft",
            criteria={"marketing_eligibility": "Eligible only"},
            suppression_policy={"default": "eligible_only"},
            message_variants=[],
            channel_cascade=[],
            send_window={},
        )

    def list_campaigns(
        self,
        *,
        owner_email: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> CampaignListResponse:
        _ = (owner_email, status, limit)
        return CampaignListResponse(
            campaigns=[self._campaign("11111111-1111-4111-8111-111111111111")]
        )

    def patch_status(
        self,
        portfolio_id: str,
        payload: CampaignStatusPatchRequest,
        *,
        actor: str | None = None,
    ) -> CampaignSummary:
        _ = actor
        return self._campaign(portfolio_id).model_copy(update={"status": payload.status})


class InProcessMockAnalyticsRepository:
    """Test fixture implementing ``AnalyticsRepository`` from synthetic rows."""

    @staticmethod
    def _is_itm(borrower: Borrower360) -> bool:
        return "itm" in borrower.segment_codes

    def executive(self, filters: AnalyticsFilters | None = None) -> ExecutiveAnalyticsResponse:
        _ = filters
        borrowers = list(mock_data.BORROWERS)
        addressable = len(borrowers)
        itm = sum(1 for b in borrowers if self._is_itm(b))
        high = sum(1 for b in borrowers if b.opportunity_score >= 75)
        recommended = sum(1 for b in borrowers if b.recommended_offer_code != "nurture")
        totals = FunnelTotals(
            snapshot_date="2026-05-18",
            addressable_borrowers=addressable,
            in_the_money_borrowers=itm,
            high_opportunity_borrowers=high,
            offer_recommended_borrowers=recommended,
            approved_borrowers=0,
            actioned_borrowers=0,
        )
        stages = [
            FunnelStage(stage="Addressable", stage_order=1, borrower_count=addressable),
            FunnelStage(stage="In the Money", stage_order=2, borrower_count=itm),
            FunnelStage(stage="High Opportunity", stage_order=3, borrower_count=high),
            FunnelStage(stage="Offer Recommended", stage_order=4, borrower_count=recommended),
            FunnelStage(stage="Approved", stage_order=5, borrower_count=0),
            FunnelStage(stage="Actioned", stage_order=6, borrower_count=0),
        ]
        buckets: dict[int, int] = {}
        for borrower in borrowers:
            bucket = int(borrower.opportunity_score // 5 * 5)
            buckets[bucket] = buckets.get(bucket, 0) + 1
        return ExecutiveAnalyticsResponse(
            totals=totals,
            stages=stages,
            score_distribution=[
                ScoreBucket(score_bucket=bucket, borrower_count=count)
                for bucket, count in sorted(buckets.items())
            ],
        )

    def geography(self, filters: AnalyticsFilters | None = None) -> GeographyAnalyticsResponse:
        _ = filters
        borrowers = list(mock_data.BORROWERS)
        by_state: dict[str, list[Borrower360]] = {}
        by_zip: dict[tuple[str, str, str], list[Borrower360]] = {}
        for borrower in borrowers:
            by_state.setdefault(borrower.state, []).append(borrower)
            by_zip.setdefault((borrower.state, borrower.zip, borrower.city), []).append(borrower)

        return GeographyAnalyticsResponse(
            state_opportunities=[
                StateOpportunityRow(
                    state=state,
                    borrower_count=len(rows),
                    mean_opportunity_score=round(sum(b.opportunity_score for b in rows) / len(rows)),
                    in_the_money_borrowers=sum(1 for b in rows if self._is_itm(b)),
                )
                for state, rows in sorted(by_state.items())
            ],
            state_avm_values=[
                StateAvmValueRow(
                    state=state,
                    total_avm_value_usd=sum(b.avm_value for b in rows),
                    total_lien_balance_usd=sum(b.current_lien_balance for b in rows),
                    total_equity_usd=sum(b.equity_estimate for b in rows),
                )
                for state, rows in sorted(by_state.items())
            ],
            top_zips=[
                TopZipOpportunityRow(
                    state=state,
                    zip=zip_code,
                    city=city,
                    borrower_count=len(rows),
                    in_the_money_borrowers=sum(1 for b in rows if self._is_itm(b)),
                    mean_opportunity_score=round(sum(b.opportunity_score for b in rows) / len(rows)),
                    mean_rate_spread_bps=round(sum(b.rate_spread_bps for b in rows) / len(rows)),
                )
                for (state, zip_code, city), rows in sorted(
                    by_zip.items(),
                    key=lambda item: sum(1 for b in item[1] if self._is_itm(b)),
                    reverse=True,
                )[:20]
                if any(self._is_itm(b) for b in rows)
            ],
        )

    def economics(self, filters: AnalyticsFilters | None = None) -> EconomicsAnalyticsResponse:
        _ = filters
        borrowers = list(mock_data.BORROWERS)
        spread_buckets: dict[int, int] = {}
        for borrower in borrowers:
            if -100 <= borrower.rate_spread_bps <= 400:
                bucket = int((borrower.rate_spread_bps // 25) * 25)
                spread_buckets[bucket] = spread_buckets.get(bucket, 0) + 1

        return EconomicsAnalyticsResponse(
            rate_spread_histogram=[
                RateSpreadBucket(spread_bucket_bps=bucket, borrower_count=count)
                for bucket, count in sorted(spread_buckets.items())
            ],
            equity_vs_spread=[
                EquitySpreadPoint(
                    borrower_id=borrower.borrower_id,
                    display_name=borrower.display_name,
                    segment=borrower.segment_codes[0] if borrower.segment_codes else "none",
                    state=borrower.state,
                    equity_pct=max(0, min(100, int(round(100.0 * borrower.equity_estimate / borrower.avm_value)))),
                    rate_spread_bps=borrower.rate_spread_bps,
                    opportunity_score=borrower.opportunity_score,
                )
                for borrower in borrowers
                if -100 <= borrower.rate_spread_bps <= 400
            ],
            top_borrowers=[
                TopBorrowerAnalyticsRow(
                    borrower_id=borrower.borrower_id,
                    display_name=borrower.display_name,
                    state=borrower.state,
                    city=borrower.city,
                    opportunity_score=borrower.opportunity_score,
                    rate_spread_bps=borrower.rate_spread_bps,
                    equity_pct=max(0, min(100, int(round(100.0 * borrower.equity_estimate / borrower.avm_value)))),
                    recommended_offer=borrower.recommended_offer,
                    rank_overall=idx,
                )
                for idx, borrower in enumerate(
                    sorted(borrowers, key=lambda b: b.opportunity_score, reverse=True)[:10],
                    start=1,
                )
            ],
        )

    def segments(self, filters: AnalyticsFilters | None = None) -> SegmentAnalyticsResponse:
        _ = filters
        segments = list(mock_data.SEGMENTS)
        by_state: dict[tuple[str, str], int] = {}
        for borrower in mock_data.BORROWERS:
            for code in borrower.segment_codes:
                key = (borrower.state, code)
                by_state[key] = by_state.get(key, 0) + 1

        overview = [
            SegmentOverviewRow(
                segment_code=segment.code,
                name=segment.name,
                borrower_count=segment.count,
                mean_opportunity_score=segment.avg_score,
                delta_vs_prior_label=segment.delta,
                description=segment.description,
                approval_rate=0.0,
                outreach_rate=0.0,
                mean_rate_spread_bps=None,
                mean_equity_pct=None,
                in_the_money_borrowers=0,
            )
            for segment in segments
        ]
        by_state_rows = [
            SegmentByStateRow(
                state=state,
                segment_code=code,
                segment_name=next((s.name for s in segments if s.code == code), code),
                borrower_count=count,
            )
            for (state, code), count in sorted(by_state.items())
        ]
        top_rows: list[TopSegmentByStateRow] = []
        states = sorted({state for state, _code in by_state})
        for state in states:
            ranked = sorted(
                [row for row in by_state_rows if row.state == state],
                key=lambda row: row.borrower_count,
                reverse=True,
            )[:3]
            for idx, row in enumerate(ranked, start=1):
                top_rows.append(TopSegmentByStateRow(**row.model_dump(), state_rank=idx))

        return SegmentAnalyticsResponse(
            scope=AnalyticsScope(
                code="full_population_pre_suppression",
                label="Full population · pre-suppression",
                description=(
                    "Synthetic analytics fixture mirrors the full-population "
                    "segment scope used by the Databricks repository."
                ),
            ),
            overview=overview,
            counts=[
                SegmentMetricRow(segment_code=s.code, segment_name=s.name, value=s.count)
                for s in segments
            ],
            average_scores=[
                SegmentMetricRow(segment_code=s.code, segment_name=s.name, value=s.avg_score)
                for s in sorted(segments, key=lambda item: item.avg_score, reverse=True)
            ],
            by_state=by_state_rows,
            top_segments_by_state=top_rows,
        )

    def signals(self, filters: AnalyticsFilters | None = None) -> SignalAnalyticsResponse:
        _ = filters
        daily_counts: dict[tuple[str, str], int] = {}
        signal_counts: dict[tuple[str, str], list[float]] = {}
        for evidence in mock_data.EVIDENCE:
            day = evidence.timestamp[:10]
            daily_key = (day, evidence.signal_type)
            daily_counts[daily_key] = daily_counts.get(daily_key, 0) + 1
            signal_key = (evidence.signal_type, evidence.source_product)
            signal_counts.setdefault(signal_key, []).append(float(evidence.confidence or 0.0))
        return SignalAnalyticsResponse(
            evidence_daily=[
                EvidenceDailyRow(event_date=day, signal_type=signal, event_count=count)
                for (day, signal), count in sorted(daily_counts.items())
            ],
            evidence_by_signal=[
                EvidenceBySignalRow(
                    signal_type=signal,
                    source_product=source,
                    source_table="mip.gold.evidence_events",
                    event_count=len(values),
                    mean_confidence=round(sum(values) / len(values), 3) if values else None,
                    confidence_source=(
                        "AVG(mip.gold.evidence_events.confidence); synthetic fixture mirrors "
                        "gold_evidence_events.sql confidence semantics."
                    ),
                )
                for (signal, source), values in sorted(signal_counts.items())
            ],
            evidence_examples=[
                SignalEvidenceExample(
                    borrower_id=mock_data.BORROWERS[0].borrower_id,
                    display_name=mock_data.BORROWERS[0].display_name,
                    state=mock_data.BORROWERS[0].state,
                    signal_type=evidence.signal_type,
                    source_product=evidence.source_product,
                    signal_value=evidence.signal_value,
                    display_text=evidence.display_text,
                    confidence=float(evidence.confidence or 0.0),
                    timestamp=evidence.timestamp,
                )
                for evidence in mock_data.EVIDENCE[:5]
            ],
        )


class InProcessMockSegmentRepository:
    """Test fixture implementing ``SegmentRepository`` from the synthetic population."""

    def list(
        self,
        portfolio_id: str | None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> list[SegmentSummary]:
        _ = portfolio_id
        borrowers = list(mock_data.BORROWERS)
        codes = [str(code).strip() for code in segment_codes or [] if str(code).strip()]
        if codes:
            if segment_mode == "all":
                borrowers = [
                    borrower for borrower in borrowers
                    if all(code in borrower.segment_codes for code in codes)
                ]
            else:
                borrowers = [
                    borrower for borrower in borrowers
                    if any(code in borrower.segment_codes for code in codes)
                ]
        if portfolio_criteria is not None:
            if portfolio_criteria.occupancy == "Owner-occupied":
                borrowers = [b for b in borrowers if b.is_owner_occupied is True]
            elif portfolio_criteria.occupancy == "Non-owner-occupied":
                borrowers = [b for b in borrowers if b.is_owner_occupied is False]
            if portfolio_criteria.marketing_eligibility == "Eligible only":
                borrowers = [b for b in borrowers if b.marketing_eligible is True]
            elif portfolio_criteria.marketing_eligibility == "Suppressed only":
                borrowers = [b for b in borrowers if b.marketing_eligible is False]
            if portfolio_criteria.consent_status == "Opt-in":
                borrowers = [b for b in borrowers if b.consent_status == "opt_in"]
            elif portfolio_criteria.consent_status == "Opt-out":
                borrowers = [b for b in borrowers if b.consent_status == "opt_out"]
            elif portfolio_criteria.consent_status == "Unknown":
                borrowers = [b for b in borrowers if b.consent_status == "unknown"]
        counts: dict[str, int] = {}
        scores: dict[str, list[int]] = {}
        for borrower in borrowers:
            for code in borrower.segment_codes:
                counts[code] = counts.get(code, 0) + 1
                scores.setdefault(code, []).append(borrower.opportunity_score)
        return [
            segment.model_copy(
                update={
                    "count": counts.get(segment.code, 0),
                    "avg_score": round(
                        sum(scores.get(segment.code, [])) / len(scores.get(segment.code, []))
                    ) if scores.get(segment.code) else 0,
                },
            )
            for segment in mock_data.SEGMENTS
        ]


class InProcessMockLeadRepository:
    """Test fixture implementing ``LeadRepository`` from the synthetic population."""

    def list(
        self,
        segment: str | None,
        portfolio_id: str | None,
        limit: int | None = None,
        state: str | None = None,
        zip_code: str | None = None,
        county_fips: str | None = None,
        county_fipses: list[str] | None = None,
        state_codes: list[str] | None = None,
        zip_codes: list[str] | None = None,
        borrower_ids: list[str] | None = None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        target_lender_ref: str | None = None,
        cohort_id: str | None = None,
        funnel_stage: str | None = None,
        portfolio_criteria: PortfolioCriteria | None = None,
        approval_status: str | None = None,
        outreach_status: str | None = None,
        aged_days: int | None = None,
    ) -> list[LeadSummary]:
        _ = (portfolio_id, cohort_id)
        leads = [LeadSummary(**b.model_dump()) for b in mock_data.BORROWERS]
        if approval_status:
            leads = [lead for lead in leads if lead.approval_status == approval_status]
        if outreach_status:
            leads = [lead for lead in leads if lead.outreach_status == outreach_status]
        if aged_days is not None:
            leads = [lead for lead in leads if (lead.aging_days or 0) >= aged_days]
        if funnel_stage:
            leads = self._filter_funnel_stage(leads, funnel_stage)
        if portfolio_criteria is not None:
            if portfolio_criteria.occupancy == "Owner-occupied":
                leads = [lead for lead in leads if lead.is_owner_occupied is True]
            elif portfolio_criteria.occupancy == "Non-owner-occupied":
                leads = [lead for lead in leads if lead.is_owner_occupied is False]
            if portfolio_criteria.marketing_eligibility == "Eligible only":
                leads = [lead for lead in leads if lead.marketing_eligible is True]
            elif portfolio_criteria.marketing_eligibility == "Suppressed only":
                leads = [lead for lead in leads if lead.marketing_eligible is False]
            if portfolio_criteria.consent_status == "Opt-in":
                leads = [lead for lead in leads if lead.consent_status == "opt_in"]
            elif portfolio_criteria.consent_status == "Opt-out":
                leads = [lead for lead in leads if lead.consent_status == "opt_out"]
            elif portfolio_criteria.consent_status == "Unknown":
                leads = [lead for lead in leads if lead.consent_status == "unknown"]
        codes = segment_codes or ([segment] if segment else [])
        codes = [str(code).strip() for code in codes if str(code).strip()]
        if codes:
            if segment_mode == "all":
                leads = [
                    lead
                    for lead in leads
                    if all(code in lead.segment_codes for code in codes)
                ]
            else:
                leads = [
                    lead
                    for lead in leads
                    if any(code in lead.segment_codes for code in codes)
                ]
        if state:
            leads = [lead for lead in leads if lead.state == state.upper()[:2]]
        if zip_code:
            leads = [lead for lead in leads if lead.zip == zip_code]
        requested_counties = ([county_fips] if county_fips else []) + (county_fipses or [])
        if requested_counties:
            allowed_zips: set[str] = set()
            for requested_county in requested_counties:
                allowed_zips.update(
                    rollup.zip
                    for rollup in _FIXTURE_ZIP_ROLLUPS.get(str(requested_county or "")[:5], [])
                )
            leads = [lead for lead in leads if lead.zip in allowed_zips]
        elif county_fips:
            county_zips = {
                rollup.zip
                for rollup in _FIXTURE_ZIP_ROLLUPS.get(str(county_fips or "")[:5], [])
            }
            leads = [lead for lead in leads if lead.zip in county_zips]
        if state_codes:
            allowed_states = {code.upper()[:2] for code in state_codes if code}
            leads = [lead for lead in leads if lead.state in allowed_states]
        if zip_codes:
            allowed_zips = {code for code in zip_codes if code}
            leads = [lead for lead in leads if lead.zip in allowed_zips]
        if borrower_ids:
            allowed_ids = {borrower_id for borrower_id in borrower_ids if borrower_id}
            leads = [lead for lead in leads if lead.borrower_id in allowed_ids]
        if target_lender_ref and target_lender_ref != "All":
            leads = [
                lead for lead in leads
                if lead.current_lender_ref == target_lender_ref
            ]
        if limit is not None and limit > 0:
            return leads[:limit]
        return leads

    def count(
        self,
        segment: str | None,
        portfolio_id: str | None,
        limit: int | None = None,
        state: str | None = None,
        zip_code: str | None = None,
        county_fips: str | None = None,
        county_fipses: list[str] | None = None,
        state_codes: list[str] | None = None,
        zip_codes: list[str] | None = None,
        borrower_ids: list[str] | None = None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        target_lender_ref: str | None = None,
        cohort_id: str | None = None,
        funnel_stage: str | None = None,
        portfolio_criteria: PortfolioCriteria | None = None,
        approval_status: str | None = None,
        outreach_status: str | None = None,
        aged_days: int | None = None,
    ) -> int:
        _ = limit
        return len(self.list(
            segment=segment,
            portfolio_id=portfolio_id,
            limit=None,
            state=state,
            zip_code=zip_code,
            county_fips=county_fips,
            county_fipses=county_fipses,
            state_codes=state_codes,
            zip_codes=zip_codes,
            borrower_ids=borrower_ids,
            segment_codes=segment_codes,
            segment_mode=segment_mode,
            target_lender_ref=target_lender_ref,
            cohort_id=cohort_id,
            funnel_stage=funnel_stage,
            portfolio_criteria=portfolio_criteria,
            approval_status=approval_status,
            outreach_status=outreach_status,
            aged_days=aged_days,
        ))

    @staticmethod
    def _filter_funnel_stage(leads: list[LeadSummary], funnel_stage: str) -> list[LeadSummary]:
        if funnel_stage == "addressable":
            return leads
        if funnel_stage == "in_the_money":
            return [lead for lead in leads if "itm" in lead.segment_codes]
        if funnel_stage == "high_opportunity":
            return [lead for lead in leads if lead.opportunity_score >= 75]
        if funnel_stage == "offer_recommended":
            return [
                lead for lead in leads
                if lead.recommended_offer_code and lead.recommended_offer_code != "nurture"
            ]
        if funnel_stage == "approved":
            return [lead for lead in leads if lead.approval_status == "approved"]
        if funnel_stage == "actioned":
            return [lead for lead in leads if lead.outreach_status == "actioned"]
        return []


class InProcessMockBorrowerRepository:
    """Test fixture implementing ``BorrowerRepository`` from the synthetic population."""

    def get(self, borrower_id: str) -> Borrower360 | None:
        for b in mock_data.BORROWERS:
            if b.borrower_id == borrower_id:
                return b
        return None

    def evidence(self, borrower_id: str) -> list[EvidenceEvent] | None:
        borrower = self.get(borrower_id)
        if borrower is None:
            return None
        return borrower.evidence_events

    def search(self, query: str, limit: int = 10) -> list[LeadSummary]:
        q = query.strip().upper()
        state_names = {
            "CALIFORNIA": "CA",
            "COLORADO": "CO",
            "FLORIDA": "FL",
            "ILLINOIS": "IL",
            "TEXAS": "TX",
            "WASHINGTON": "WA",
        }
        state_code = q if len(q) == 2 else state_names.get(q)
        if state_code is None and len(q) >= 3:
            state_code = next((code for name, code in state_names.items() if name.startswith(q)), None)
        rows = [
            LeadSummary(**b.model_dump())
            for b in mock_data.BORROWERS
            if b.borrower_id.upper().startswith(q)
            or b.zip.startswith(query.strip())
            or b.state == state_code
            or q in b.city.upper()
            or b.clip_id == query.strip()
        ]
        return rows[:limit]


class InProcessMockOfferRepository:
    """Test fixture implementing ``OfferRepository`` from the synthetic population."""

    def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
        return mock_data.BORROWER_OFFER_INPUTS.get(borrower_id)


class InProcessMockOutreachRepository:
    """Test fixture implementing ``OutreachRepository`` from the synthetic population."""

    def find_borrower(self, borrower_id: str) -> Borrower360 | None:
        for b in mock_data.BORROWERS:
            if b.borrower_id == borrower_id:
                return b
        return None


class InProcessMockGenieAnswerRepository:
    """Test fixture deriving Genie-shaped answers from mock_population rows.

    The production codebase intentionally has no canned Genie answer catalog.
    This fixture is test-only and computes its counts/rows from
    ``tests.fixtures.mock_population`` so local UI tests still exercise the
    response shape without importing production fallback content.
    """

    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieMessageResponse:
        q = question.lower()
        rows: list[dict[str, object]]
        answer: str
        visualization: GenieVisualizationSpec | None = None
        assets = ["mip.gold.borrower_360"]
        if "heloc" in q or "equity" in q or "permit" in q:
            equity_rows = [
                b for b in mock_data.BORROWERS
                if "equity" in b.segment_codes or b.recommended_offer in {"Refinance + HELOC", "HELOC"}
            ]
            rows = [
                {
                    "borrower_id": b.borrower_id,
                    "state": b.state,
                    "zip": b.zip,
                    "recommended_offer": b.recommended_offer,
                    "opportunity_score": b.opportunity_score,
                }
                for b in equity_rows[:5]
            ]
            answer = (
                "The test fixture can size equity-backed HELOC candidates from "
                f"{len(equity_rows):,} mock borrower rows. Pending permit-driven "
                "HELOC signals remain blocked until the Building Permits share lands."
            )
            assets = ["mip.gold.borrower_360", "mip.gold.source_readiness"]
            visualization = GenieVisualizationSpec(
                kind="borrower_list",
                title="Fixture HELOC candidates",
                x="borrower_id",
                y="opportunity_score",
            )
        else:
            ranked = sorted(
                mock_data.BORROWERS,
                key=lambda b: b.opportunity_score,
                reverse=True,
            )
            rows = [
                {
                    "borrower_id": b.borrower_id,
                    "state": b.state,
                    "zip": b.zip,
                    "opportunity_score": b.opportunity_score,
                    "recommended_offer": b.recommended_offer,
                }
                for b in ranked[:5]
            ]
            answer = (
                "The test fixture returned the highest-scored mock borrower rows "
                "from the in-process borrower population."
            )
            visualization = GenieVisualizationSpec(
                kind="borrower_list",
                title="Fixture borrower ranking",
                x="borrower_id",
                y="opportunity_score",
            )
        response = GenieMessageResponse(
            conversation_id=conversation_id or "fixture-conv",
            question=question,
            answer=answer,
            source="genie",
            trusted_assets=assets,
            row_count=len(rows),
            proof=GenieProof(
                source_assets=assets,
                row_count=len(rows),
                trusted=True,
                filters=[],
                known_data_gaps=[],
                conversation_id=conversation_id or "fixture-conv",
            ),
            visualization=visualization,
            table_rows=rows,
            follow_up_questions=default_follow_up_questions(),
        )
        borrower_ids: list[str] = []
        for row in rows:
            value = row.get("borrower_id")
            if isinstance(value, str) and value.startswith("B-") and value not in borrower_ids:
                borrower_ids.append(value)
        if borrower_ids:
            response = response.model_copy(
                update={
                    "actions": [
                        GenieActionSuggestion(
                            id="save-borrowers",
                            label="Save 1 borrower",
                            action_type="save_borrowers",
                            description="Add returned borrowers to the governed saved workspace.",
                            borrower_ids=borrower_ids[:1],
                            criteria={
                                "source": "genie",
                                "source_assets": ["mip.gold.lead_population"],
                                "visualization_kind": "borrower_list",
                                "row_count": len(rows),
                            },
                        )
                    ]
                }
            )
        return response


# Fixture per-state rollups — covers a representative current-coverage shape
# so /api/geo/state-rollups returns realistic schemas under test and
# exercises the same code path the UI hits in prod (no secondary code
# branch for "no data"). Numbers are proportional to the Apr-2026 share
# probe in docs/data-sources-gap-analysis.md §1.
#
# slice13-accuracy-validation: added ``top_segment_code`` on each row so
# tests exercise the StateRollup extension that the USChoroplethMap
# consumes for its segment-filter dim logic.
_FIXTURE_STATE_ROLLUPS: list[StateRollup] = [
    StateRollup(state="IL", addressable=1860, in_the_money=720, top_tier_opportunities=420, avg_score=84, top_segment_code="itm"),
    StateRollup(state="CA", addressable=900,  in_the_money=420, top_tier_opportunities=260, avg_score=83, top_segment_code="equity"),
    StateRollup(state="FL", addressable=760,  in_the_money=320, top_tier_opportunities=200, avg_score=81, top_segment_code="investor"),
    StateRollup(state="TX", addressable=750,  in_the_money=340, top_tier_opportunities=220, avg_score=82, top_segment_code="equity"),
    StateRollup(state="WA", addressable=740,  in_the_money=300, top_tier_opportunities=190, avg_score=81, top_segment_code="itm"),
    StateRollup(state="CO", addressable=160,  in_the_money=62,  top_tier_opportunities=42,  avg_score=80, top_segment_code="itm"),
]


# Fixture county rollups keyed by state. Covers the three anchor
# counties the USChoroplethMap used to hardcode (Cook / LA / Harris) +
# a handful of siblings so tests can exercise both a populated state
# and an empty-state path.
_FIXTURE_COUNTY_ROLLUPS: dict[str, list[CountyRollup]] = {
    "IL": [
        CountyRollup(fips_5="17031", state="IL", county_name="Cook",    addressable_borrowers=620, in_the_money_borrowers=310, high_opportunity_borrowers=260, avg_opportunity_score=86, top_segment_code="itm"),
        CountyRollup(fips_5="17043", state="IL", county_name="DuPage",  addressable_borrowers=310, in_the_money_borrowers=150, high_opportunity_borrowers=120, avg_opportunity_score=82, top_segment_code="equity"),
        CountyRollup(fips_5="17097", state="IL", county_name="Lake",    addressable_borrowers=265, in_the_money_borrowers=120, high_opportunity_borrowers=95,  avg_opportunity_score=80, top_segment_code="itm"),
    ],
    "CA": [
        CountyRollup(fips_5="06037", state="CA", county_name="Los Angeles", addressable_borrowers=720, in_the_money_borrowers=340, high_opportunity_borrowers=290, avg_opportunity_score=85, top_segment_code="equity"),
        CountyRollup(fips_5="06073", state="CA", county_name="San Diego",   addressable_borrowers=340, in_the_money_borrowers=150, high_opportunity_borrowers=120, avg_opportunity_score=81, top_segment_code="itm"),
    ],
    "TX": [
        CountyRollup(fips_5="48201", state="TX", county_name="Harris",  addressable_borrowers=540, in_the_money_borrowers=250, high_opportunity_borrowers=210, avg_opportunity_score=83, top_segment_code="equity"),
        CountyRollup(fips_5="48113", state="TX", county_name="Dallas",  addressable_borrowers=420, in_the_money_borrowers=190, high_opportunity_borrowers=160, avg_opportunity_score=82, top_segment_code="itm"),
    ],
}


# Fixture ZIP rollups keyed by county FIPS. Covers Cook County's anchor
# ZIPs so the ZIP-drill test exercises both populated and empty paths.
_FIXTURE_ZIP_ROLLUPS: dict[str, list[ZipRollup]] = {
    "17031": [
        ZipRollup(zip="60611", state="IL", county_fips_5="17031", addressable_borrowers=94, avg_opportunity_score=94, top_segment_code="itm",    sample_borrower_id="B-48291"),
        ZipRollup(zip="60647", state="IL", county_fips_5="17031", addressable_borrowers=72, avg_opportunity_score=82, top_segment_code="equity", sample_borrower_id="B-48294"),
        ZipRollup(zip="60613", state="IL", county_fips_5="17031", addressable_borrowers=58, avg_opportunity_score=80, top_segment_code="itm",    sample_borrower_id="B-48295"),
    ],
}


class InProcessMockGeoRepository:
    """Test fixture implementing ``GeoRepository`` from the synthetic rollups."""

    def state_rollups(
        self,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> StateRollupResponse:
        _ = (segment_codes, segment_mode, portfolio_criteria)
        return StateRollupResponse(
            rollups=list(_FIXTURE_STATE_ROLLUPS),
            snapshot_date="2026-04-22",
        )

    def county_rollups(
        self,
        state: str,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> CountyRollupResponse:
        _ = (segment_codes, segment_mode, portfolio_criteria)
        normalised = str(state or "").upper()[:2]
        return CountyRollupResponse(
            state=normalised,
            rollups=list(_FIXTURE_COUNTY_ROLLUPS.get(normalised, [])),
            snapshot_date="2026-04-22",
        )

    def zip_rollups(
        self,
        fips_5: str,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> ZipRollupResponse:
        _ = (segment_codes, segment_mode, portfolio_criteria)
        normalised = str(fips_5 or "")[:5]
        return ZipRollupResponse(
            fips_5=normalised,
            rollups=list(_FIXTURE_ZIP_ROLLUPS.get(normalised, [])),
            snapshot_date="2026-04-22",
        )
