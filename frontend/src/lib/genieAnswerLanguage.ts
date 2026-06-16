export function normalizeGenieAnswerLanguage(answer: string): string {
  if (!answer) return answer;
  return answer
    .replace(
      /\bborrowers who are considered in-the-money for refinancing\b/gi,
      'borrowers passing the refinance-economics screen',
    )
    .replace(
      /\bin-the-money refinance candidates\b/gi,
      'borrowers passing the refinance-economics screen',
    )
    .replace(
      /\bin-the-money borrowers\b/gi,
      'borrowers passing the refinance-economics screen',
    )
    .replace(
      /\bborrowers currently in-the-money\b/gi,
      'borrowers passing the refinance-economics screen',
    )
    .replace(/\bcurrently in-the-money\b/gi, 'passing the refinance-economics screen')
    .replace(/\bin[- ]the[- ]money\b/gi, 'passing the refinance-economics screen');
}
