import { useApp } from '../AppContext';

/**
 * ConfidenceMeter — prototype `.conf` + `.conf__bars` BEM. Five bars; number
 * of filled bars = round(value / 20). Tier classifies color (>=80 green,
 * >=60 amber, else red). The user-facing label is "signal" because this is a
 * deterministic average of sub-scores, not a statistical confidence interval.
 * Hides itself when showConfidence is toggled off in the Console.
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
      title="Signal strength is a deterministic average of the five scoring sub-scores, not a statistical confidence interval."
      aria-label={`Recommendation signal strength ${value} percent.`}
    >
      <span className="conf__bars">
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className={`conf__bar ${i < filled ? 'on' : ''}`} />
        ))}
      </span>
      {!compact && <span>Signal {value}%</span>}
    </span>
  );
}
