import { openGenie } from '../../lib/genieOpen';
import { Icon } from '../Icon';

/**
 * "Ask Genie about this" entry point (audit 2026-09-21 `genie-04`, phase 1).
 *
 * A small ghost button that opens the floating Genie panel with a REVIEWED
 * prompt from `lib/genieContext` already in the composer. It never submits:
 * the user reads the question, edits it if they like, and presses Ask, so the
 * prompt of record is exactly what the guards scan.
 *
 * `prompt` is null when the surface has no reviewed template (an unknown KPI
 * label, an unregistered or gated segment, a state outside the USPS list),
 * and then nothing renders: a template is never improvised from on-screen
 * text.
 *
 * Provider-free (a plain `openGenie` call, no `useApp`), so a KPI card
 * rendered on its own in a unit test or Storybook still carries it.
 *
 * `.genie-ask-about` is a documented BEM extension: the prototype's `.kpi`
 * and `.seg-card` blocks (design_files/index.html) carry no assistant
 * affordance. The button reuses `.btn .btn--ghost .btn--sm` for its shape;
 * the `icon` variant is the compact form for the dense KPI and segment cards
 * (its accessible name and tooltip still say what it does).
 */
interface GenieAskAboutProps {
  /** Reviewed prompt from `lib/genieContext`; null renders nothing. */
  prompt: string | null;
  /** What "this" is, for the accessible name: "KPI: Addressable population". */
  subject: string;
  /** `icon`: sparkle only (dense cards). `text`: sparkle + "Ask Genie". */
  variant?: 'icon' | 'text';
}

export function GenieAskAbout({ prompt, subject, variant = 'text' }: GenieAskAboutProps) {
  if (!prompt) return null;
  const className = ['btn', 'btn--ghost', 'btn--sm', 'genie-ask-about', variant === 'icon' ? 'genie-ask-about--icon' : '']
    .filter(Boolean)
    .join(' ');
  return (
    <button
      type="button"
      className={className}
      aria-label={`Ask Genie about this ${subject}`}
      title={`Ask Genie: ${prompt}`}
      onClick={() => openGenie({ prompt })}
    >
      <Icon name="sparkle" size={12} />
      {variant === 'text' && 'Ask Genie'}
    </button>
  );
}
