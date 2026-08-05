export interface CampaignRecommendationResponse {
  generation_mode: 'supervisor' | 'reviewed_fallback';
  generator_label: string;
  performance_status: 'qualified' | 'insufficient_sample' | 'unavailable';
  audience_summary: string;
  strategy: string;
  variants: Array<{
    variant_name: 'Benefit-led' | 'Guidance-led';
    subject: string;
    body: string;
    hypothesis: string;
    provenance_token: string | null;
  }>;
  holdout_pct: number;
  evidence: Array<{
    label: string;
    value: string;
    source_asset: string;
  }>;
  warnings: string[];
}
