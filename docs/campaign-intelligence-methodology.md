# Campaign intelligence methodology

The campaign surfaces separate facts, forecasts, strategy, and operator inputs.
They must never present a typed assumption as observed performance.

## Evidence hierarchy

1. **Cohort facts** come from the exact Portfolio Builder criteria over governed
   gold assets: eligible borrower count, primary-offer mix, current-lien
   balance, modeled equity, and rate spread.
2. **Observed performance** comes from the lender's trailing 90-day Lakebase
   call dispositions and funded-outcome ledger. Application and funding rates
   are withheld when those denominators are absent.
3. **Forecasts** apply those observed rates to the current cohort. Revenue and
   acquisition cost are withheld until the tenant supplies its own revenue
   rate and cost per lead; there is no product-wide default.
4. **Supervisor recommendations** use aggregate cohort facts only. Model output
   may propose audience framing, two distinct message variants, a test
   hypothesis, and a holdout. The server validates the prose and owns evidence,
   offer rules, eligibility, disclosures, and approval.

## Message design rules

- Use plain language and one specific potential benefit.
- Preserve borrower autonomy: invite a review; do not presume that changing a
  loan is the right choice.
- Use one low-friction call to action.
- Do not use false urgency, scarcity, guarantees, unsupported savings, quoted
  rates, targeting-language, or unexplained mortgage jargon.
- Compare genuinely different hypotheses, such as benefit-led versus
  guidance-led framing, rather than cosmetic subject-line changes.
- Keep a randomized holdout so performance can be measured against no outreach.

These rules align the product with the FTC's guidance that disclosures and
advertising claims be clear, unambiguous, and understandable, and with the
CFPB's plain-language guidance for consumer financial communications:

- FTC, [Native Advertising: A Guide for Businesses](https://www.ftc.gov/business-guidance/resources/native-advertising-guide-businesses)
- FTC, [Digital advertising disclosure guidance](https://www.ftc.gov/news-events/news/press-releases/2013/03/ftc-staff-revises-online-advertising-disclosure-guidelines)
- CFPB, [Plain-language report](https://files.consumerfinance.gov/f/201307_cfpb_report_plain-language.pdf)

## Measurement

Every experiment should pre-register the audience criteria, variants, holdout,
send window, and primary outcome. Compare qualified response, application
start, submitted application, funded loan, opt-out, and complaint rates. Avoid
promoting a winner on opens alone. Segment-level conclusions require enough
events to be stable; otherwise the UI should continue to show the observed
counts and withhold a winner.
