import { useState, type ChangeEvent } from 'react';
import { Link } from 'react-router';
import { Icon } from '../components/Icon';
import { Button, EvidenceChip, SurfaceTitle } from '../components/Primitives';
import { Field, FieldReadout } from '../components/ui/Field';
import { drawerForAsset } from '../lib/drawerSources';
import type { CampaignRecommendationResponse } from '../types';
import { publicAgentResponsesText } from '../lib/agentLabels';
import {
  CAMPAIGN_NUMERIC_BOUNDS,
  campaignNumericNotice,
  normalizeCampaignNumericValue,
  type CampaignNumericField,
  type CampaignSetupState,
} from './portfolio-builder.logic';

/** The unit each numeric editor shows beside its control, and says in words (critic-04). */
const NUMERIC_UNITS: Record<CampaignNumericField, { prefix?: string; suffix?: string; unit: string }> = {
  holdoutPct: { suffix: '%', unit: 'percent' },
  budget: { prefix: '$', unit: 'US dollars' },
  emailCost: { prefix: '$', unit: 'US dollars' },
  smsCost: { prefix: '$', unit: 'US dollars' },
  mailCost: { prefix: '$', unit: 'US dollars' },
};
const COPY_NOT_SET = 'Not set. Apply a recommendation to fill it.';

type CampaignField = Exclude<
  keyof CampaignSetupState,
  | 'marketHouseholdTogether'
  | 'generationMode'
  | 'generatorLabel'
  | 'provenanceTokenA'
  | 'provenanceTokenB'
  | 'subjectA'
  | 'subjectB'
  | 'bodyA'
  | 'bodyB'
>;

export function CampaignSetupPanel({
  setup,
  recommendation,
  recommendationPending,
  recommendationError,
  recommendationFetching,
  canRecommend,
  canAccessAdmin = false,
  onFieldChange,
  onNumericFieldCommit,
  onToggleHouseholdDedup,
  onRegenerate,
  onApply,
}: {
  setup: CampaignSetupState;
  recommendation?: CampaignRecommendationResponse;
  recommendationPending: boolean;
  recommendationError: boolean;
  recommendationFetching: boolean;
  canRecommend: boolean;
  canAccessAdmin?: boolean;
  onFieldChange: (
    key: CampaignField,
  ) => (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
  onNumericFieldCommit: (key: CampaignNumericField, value: string) => void;
  onToggleHouseholdDedup: () => void;
  onRegenerate: () => void;
  onApply: () => void;
}) {
  const recommendationActionable = Boolean(
    canRecommend
    && recommendation
    && !recommendationPending
    && !recommendationError
    && !recommendationFetching,
  );
  const applyRecommendation = () => {
    if (!recommendationActionable) return;
    onApply();
  };
  return (
    <div className="surface mt-4">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="send" size={14} />
          </div>
          <div>
            <SurfaceTitle>Campaign setup</SurfaceTitle>
            <div className="muted fs-12">
              Evidence-backed variants, measured assumptions, holdout, and delivery controls.
            </div>
          </div>
        </div>
        <span className="chip chip--success">eligible only · 30d cap</span>
      </div>
      <div className="surface__body">
        <div className="campaign-recommendation" aria-live="polite">
          <div className="campaign-recommendation__header">
            <div>
              <div className="h-5">Growth Agent recommendation</div>
              <div className="muted fs-12">
                Plain-language A/B strategy over the selected cohort, with team performance used only when its sample is qualified.
              </div>
            </div>
            <div className="campaign-recommendation__actions">
              {recommendation && (
                <span className={`chip ${recommendation.generation_mode === 'supervisor' ? 'chip--success' : 'chip--neutral'}`}>
                  {publicAgentResponsesText(recommendation.generator_label)}
                </span>
              )}
              {recommendation && (
                <span className={`chip ${recommendation.performance_status === 'qualified' ? 'chip--success' : 'chip--neutral'}`}>
                  {recommendation.performance_status === 'qualified'
                    ? 'Qualified team performance'
                    : recommendation.performance_status === 'insufficient_sample'
                      ? 'Cohort data only · sample too small'
                      : 'Cohort data only · operations unavailable'}
                </span>
              )}
              <Button
                variant="ghost"
                size="sm"
                icon="bolt"
                onClick={() => {
                  onRegenerate();
                }}
                disabled={!canRecommend || recommendationFetching}
              >
                {recommendationFetching ? 'Analyzing…' : 'Regenerate'}
              </Button>
              <Button
                variant="primary"
                size="sm"
                icon="check"
                onClick={applyRecommendation}
                disabled={!recommendationActionable}
              >
                Apply variants
              </Button>
            </div>
          </div>
          {!canRecommend ? (
            <div className="muted fs-12">
              Run a non-empty portfolio build to generate cohort-specific strategy and copy.
            </div>
          ) : recommendationPending ? (
            <div className="campaign-recommendation__loading muted fs-12">Analyzing the selected cohort…</div>
          ) : recommendationError ? (
            <div className="status-callout status-callout--warning">
              Campaign intelligence is temporarily unavailable. Existing editor values are unchanged.
            </div>
          ) : recommendation ? (
            <>
              <p className="campaign-recommendation__audience">{recommendation.audience_summary}</p>
              <p className="campaign-recommendation__strategy">{recommendation.strategy}</p>
              <section
                className="campaign-recommendation__hypotheses"
                aria-labelledby="campaign-variant-hypotheses-title"
              >
                <div
                  id="campaign-variant-hypotheses-title"
                  className="h-5"
                  role="heading"
                  aria-level={3}
                >
                  Message hypotheses
                </div>
                <dl className="campaign-recommendation__hypothesis-list">
                  {recommendation.variants.map((variant) => (
                    <div className="campaign-recommendation__hypothesis" key={variant.variant_name}>
                      <dt>
                        <strong>{variant.variant_name}</strong>
                        <span className="chip chip--neutral">Hypothesis</span>
                      </dt>
                      <dd>{variant.hypothesis}</dd>
                    </div>
                  ))}
                </dl>
              </section>
              <div className="campaign-recommendation__evidence" aria-label="Recommendation evidence">
                {recommendation.evidence.map((row) => {
                  const source = drawerForAsset(row.source_asset);
                  const content = (
                    <span className="campaign-evidence">
                      <span>{row.label}</span>
                      <strong>{row.value}</strong>
                      <code>{row.source_asset}</code>
                    </span>
                  );
                  return source ? (
                    <EvidenceChip
                      key={`${row.source_asset}:${row.label}`}
                      source={source}
                      title={`Inspect ${row.source_asset}`}
                    >
                      {content}
                    </EvidenceChip>
                  ) : (
                    <span
                      key={`${row.source_asset}:${row.label}`}
                      className="campaign-evidence"
                      aria-label={`${row.label}: evidence destination unavailable`}
                    >
                      <span>{row.label}</span>
                      <strong>{row.value}</strong>
                      <code>{row.source_asset}</code>
                    </span>
                  );
                })}
              </div>
              {recommendation.warnings.map((warning) => (
                <div className="muted fs-12" key={warning}>{warning}</div>
              ))}
            </>
          ) : null}
        </div>
        <div className="campaign-setup">
          {/* critic-04: copy the operator cannot edit reads as text, never as an input. */}
          <FieldReadout className="campaign-setup__field" label="Benefit-led subject" value={setup.subjectA} empty={COPY_NOT_SET} />
          <FieldReadout className="campaign-setup__field" label="Guidance-led subject" value={setup.subjectB} empty={COPY_NOT_SET} />
          <FieldReadout className="campaign-setup__field campaign-setup__field--wide" label="Benefit-led message" value={setup.bodyA} empty={COPY_NOT_SET} multiline />
          <FieldReadout className="campaign-setup__field campaign-setup__field--wide" label="Guidance-led message" value={setup.bodyB} empty={COPY_NOT_SET} multiline />
          <div className="campaign-setup__field campaign-setup__field--wide muted fs-12">
            Borrower copy is rendered from reviewed server templates. Apply or regenerate the
            recommendation to change it.
          </div>
          <CampaignNumericFieldEditor label="Holdout % (0-50)" field="holdoutPct" value={setup.holdoutPct} onChange={onFieldChange('holdoutPct')} onCommit={onNumericFieldCommit} />
          <CampaignTimeField label="Send start" value={setup.startLocal} onChange={onFieldChange('startLocal')} />
          <CampaignTimeField label="Send end" value={setup.endLocal} onChange={onFieldChange('endLocal')} />
          <CampaignNumericFieldEditor label="Budget" field="budget" value={setup.budget} onChange={onFieldChange('budget')} onCommit={onNumericFieldCommit} placeholder="optional" />
          <CampaignNumericFieldEditor label="Email cost" field="emailCost" value={setup.emailCost} onChange={onFieldChange('emailCost')} onCommit={onNumericFieldCommit} />
          <CampaignNumericFieldEditor label="SMS cost" field="smsCost" value={setup.smsCost} onChange={onFieldChange('smsCost')} onCommit={onNumericFieldCommit} />
          <CampaignNumericFieldEditor label="Mail cost" field="mailCost" value={setup.mailCost} onChange={onFieldChange('mailCost')} onCommit={onNumericFieldCommit} />
          <div className="campaign-setup__field campaign-setup__field--wide">
            <div className="campaign-setup__toggle">
              <div className="campaign-setup__toggle-copy">
                <span>market the household together</span>
                <p>
                  One eligible primary contact per household enters the campaign;
                  suppressed co-owners are counted in the summary.
                </p>
              </div>
              <button
                type="button"
                className={`switch ${setup.marketHouseholdTogether ? 'on' : ''}`}
                onClick={onToggleHouseholdDedup}
                aria-pressed={setup.marketHouseholdTogether}
                aria-label="market the household together"
              />
            </div>
          </div>
        </div>
        <div className="campaign-setup__meta">
          <span>Email → SMS after 3 days → direct mail after 10 days</span>
          <span>
            {canAccessAdmin ? (
              <><Link to="/admin-config#data-operations">Admin Data Operations</Link> shows refresh status.</>
            ) : (
              <>Contact an administrator to review refresh status.</>
            )}
          </span>
          <span>Tue-Thu · borrower local time</span>
        </div>
      </div>
    </div>
  );
}

function CampaignNumericFieldEditor({
  label,
  field,
  value,
  onChange,
  onCommit,
  placeholder,
}: {
  label: string;
  field: CampaignNumericField;
  value: string;
  onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
  onCommit: (field: CampaignNumericField, value: string) => void;
  placeholder?: string;
}) {
  const bounds = CAMPAIGN_NUMERIC_BOUNDS[field];
  // critic-04: a clamp or a rounding is announced in the field's polite
  // notice ('Capped at 50%', 'Rounded to 12.35%'), never applied silently.
  // The notice belongs to the value it was written for: the next in-range
  // commit, a keystroke, or a value set from outside the field (Apply
  // variants) clears it.
  const [notice, setNotice] = useState<{ text: string; value: string } | null>(null);
  if (notice !== null && notice.value !== value) setNotice(null);
  return (
    <Field className="campaign-setup__field" label={label} notice={notice?.text ?? null} {...NUMERIC_UNITS[field]}>
      {(control) => (
        <input
          {...control}
          className="form-input"
          value={value}
          onChange={onChange}
          type="number"
          inputMode="decimal"
          placeholder={placeholder}
          min={bounds.min}
          max={bounds.max}
          step={bounds.step}
          onBlur={(event) => {
            const normalized = normalizeCampaignNumericValue(field, event.currentTarget.value);
            const text = campaignNumericNotice(field, normalized);
            setNotice(text === null ? null : { text, value: normalized.value });
            onCommit(field, normalized.value);
          }}
        />
      )}
    </Field>
  );
}

function CampaignTimeField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
}) {
  return (
    <Field className="campaign-setup__field" label={label}>
      {(control) => <input {...control} className="form-input" type="time" value={value} onChange={onChange} />}
    </Field>
  );
}
