import type { ChangeEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Icon } from '../components/Icon';
import { Button, EvidenceChip } from '../components/Primitives';
import { drawerForAsset } from '../lib/drawerSources';
import type { CampaignRecommendationResponse } from '../types';
import type { CampaignSetupState } from './portfolio-builder.logic';

type CampaignField = Exclude<
  keyof CampaignSetupState,
  'marketHouseholdTogether' | 'generationMode' | 'generatorLabel'
>;

export function CampaignSetupPanel({
  setup,
  recommendation,
  recommendationPending,
  recommendationError,
  recommendationFetching,
  canRecommend,
  onFieldChange,
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
  onFieldChange: (
    key: CampaignField,
  ) => (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
  onToggleHouseholdDedup: () => void;
  onRegenerate: () => void;
  onApply: () => void;
}) {
  const navigate = useNavigate();
  return (
    <div className="surface mt-4">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="send" size={14} />
          </div>
          <div>
            <div className="h-4">Campaign setup</div>
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
                  {recommendation.generator_label}
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
                icon="sparkle"
                onClick={onRegenerate}
                disabled={!canRecommend || recommendationFetching}
              >
                {recommendationFetching ? 'Analyzing…' : 'Regenerate'}
              </Button>
              <Button
                variant="primary"
                size="sm"
                icon="check"
                onClick={onApply}
                disabled={!recommendation}
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
              <div className="campaign-recommendation__evidence" aria-label="Recommendation evidence">
                {recommendation.evidence.map((row) => (
                  <EvidenceChip
                    key={`${row.source_asset}:${row.label}`}
                    source={drawerForAsset(row.source_asset) ?? undefined}
                    onClick={drawerForAsset(row.source_asset)
                      ? undefined
                      : () => navigate('/admin-config#audit')}
                    title={`Inspect ${row.source_asset}`}
                  >
                    <span className="campaign-evidence">
                    <span>{row.label}</span>
                    <strong>{row.value}</strong>
                    <code>{row.source_asset}</code>
                    </span>
                  </EvidenceChip>
                ))}
              </div>
              {recommendation.warnings.map((warning) => (
                <div className="muted fs-12" key={warning}>{warning}</div>
              ))}
            </>
          ) : null}
        </div>
        <div className="campaign-setup">
          <CampaignTextField label="Benefit-led subject" value={setup.subjectA} onChange={onFieldChange('subjectA')} maxLength={120} />
          <CampaignTextField label="Guidance-led subject" value={setup.subjectB} onChange={onFieldChange('subjectB')} maxLength={120} />
          <CampaignTextField label="Benefit-led message" value={setup.bodyA} onChange={onFieldChange('bodyA')} maxLength={700} multiline />
          <CampaignTextField label="Guidance-led message" value={setup.bodyB} onChange={onFieldChange('bodyB')} maxLength={700} multiline />
          <CampaignTextField label="Holdout %" value={setup.holdoutPct} onChange={onFieldChange('holdoutPct')} inputMode="decimal" />
          <CampaignTextField label="Send start" value={setup.startLocal} onChange={onFieldChange('startLocal')} type="time" />
          <CampaignTextField label="Send end" value={setup.endLocal} onChange={onFieldChange('endLocal')} type="time" />
          <CampaignTextField label="Budget" value={setup.budget} onChange={onFieldChange('budget')} inputMode="decimal" placeholder="optional" />
          <CampaignTextField label="Email cost" value={setup.emailCost} onChange={onFieldChange('emailCost')} inputMode="decimal" />
          <CampaignTextField label="SMS cost" value={setup.smsCost} onChange={onFieldChange('smsCost')} inputMode="decimal" />
          <CampaignTextField label="Mail cost" value={setup.mailCost} onChange={onFieldChange('mailCost')} inputMode="decimal" />
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
          <span><Link to="/admin-config#data-operations">Admin Data Operations</Link> shows refresh status.</span>
          <span>Tue-Thu · borrower local time</span>
        </div>
      </div>
    </div>
  );
}

function CampaignTextField({
  label,
  value,
  onChange,
  maxLength,
  multiline = false,
  type,
  inputMode,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
  maxLength?: number;
  multiline?: boolean;
  type?: string;
  inputMode?: 'decimal';
  placeholder?: string;
}) {
  const className = `campaign-setup__field${multiline ? ' campaign-setup__field--wide' : ''}`;
  return (
    <label className={className}>
      <span>{label}</span>
      {multiline ? (
        <textarea
          className="form-input campaign-setup__textarea"
          value={value}
          onChange={onChange}
          maxLength={maxLength}
        />
      ) : (
        <input
          className="form-input"
          value={value}
          onChange={onChange}
          maxLength={maxLength}
          type={type}
          inputMode={inputMode}
          placeholder={placeholder}
        />
      )}
    </label>
  );
}
