export const DATABRICKS_AGENT_RESPONSES_LABEL = 'Databricks Agent Responses';

const AGENT_RESPONSES_ALIASES = new Set([
  'Supervisor-composed notification',
  'Databricks Agent Responses endpoint',
  'Agent Responses endpoint',
  'Databricks Supervisor Agent',
  'Supervisor Agent',
  'Multi-agent framework',
  'Agent framework',
  'agent_framework_supervisor',
  'Agent endpoint-generated recommendation',
]);

export function publicAgentResponsesText(value: string | null | undefined): string {
  if (!value) return '';
  const normalized = value.trim();
  return AGENT_RESPONSES_ALIASES.has(normalized)
    ? DATABRICKS_AGENT_RESPONSES_LABEL
    : normalized;
}
