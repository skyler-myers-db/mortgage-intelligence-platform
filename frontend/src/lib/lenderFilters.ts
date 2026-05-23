export const LENDER_RELATIONSHIP_OPTIONS = ['All', 'Current customer', 'Former customer', 'Competitor customer'] as const;

const COMPETITOR_LENDER_REF_RE = /^Competitor ([A-Z]|Other)$/;

export function isPublicLenderRef(
  value: string | undefined | null,
  allowedRefs: readonly string[] = [],
): boolean {
  const trimmed = value?.trim();
  if (!trimmed) return false;
  if (trimmed === 'All' || COMPETITOR_LENDER_REF_RE.test(trimmed)) return true;
  return allowedRefs.includes(trimmed);
}
