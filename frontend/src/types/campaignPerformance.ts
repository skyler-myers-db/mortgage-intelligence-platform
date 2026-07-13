export interface CampaignPerformanceFunnelResponse {
  from_date: string;
  to_date: string;
  unique_contacts_reached: number;
  unique_application_starts: number;
  unique_applications_submitted: number;
  unique_closed_funded: number;
  methodology: 'same_borrower_nested_funnel';
}
