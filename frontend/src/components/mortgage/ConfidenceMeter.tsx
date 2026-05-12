import { useApp } from '../AppContext';

/**
 * ConfidenceMeter — prototype `.conf` + `.conf__bars` BEM. Five bars; number
 * of filled bars = round(value / 20). Tier classifies color (>=80 green,
 * >=60 amber, else red). Hides itself when showConfidence is toggled off in
 * the Console.
 */

interface ConfidenceMeterProps {
  value: number;
  compact?: boolean;
}

export function ConfidenceMeter({ value, compact }: ConfidenceMeterProps) {
  const { showConfidence } = useApp();
  if (!showConfidence) return null;
  const filled = Math.max(0, Math.min(5, Math.round(value / 20)));
  const tier = value >= 80 ? 'high' : value >= 60 ? 'med' : 'low';
  return (
    <span
      className={`conf conf--${tier}`}
      title="Confidence reflects source coverage and model certainty for this recommendation."
      aria-label={`Recommendation confidence ${value} percent.`}
    >
      <span className="conf__bars">
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className={`conf__bar ${i < filled ? 'on' : ''}`} />
        ))}
      </span>
      {!compact && <span>{value}% conf.</span>}
    </span>
  );
}
