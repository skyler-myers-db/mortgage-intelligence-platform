const FOLLOW_UP_PREFIXES = [
  'why',
  'show that',
  'show those',
  'show them',
  'break that',
  'break those',
  'only ',
  'filter ',
  'drill ',
  'turn that',
  'turn those',
  'what about',
  'compare that',
  'compare those',
];

export function isGenieFollowUpQuestion(question: string): boolean {
  const q = question.trim().toLowerCase().replace(/\s+/g, ' ');
  if (!q) return false;
  const wordCount = q.split(' ').length;
  if (wordCount <= 4 && /^(why|where|which|how|show|only|filter)\b/.test(q)) {
    return true;
  }
  return FOLLOW_UP_PREFIXES.some((prefix) => q.startsWith(prefix));
}
