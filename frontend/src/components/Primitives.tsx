import type { ButtonHTMLAttributes, PropsWithChildren, Ref } from 'react';
import { Icon, type IconName } from './Icon';
import { useApp, type DrawerSource } from './AppContext';
import { useEvidenceHoverCard } from './EvidenceHoverCard';
import { freshnessBucket, FRESHNESS_LABEL, type FreshnessBucket } from './freshness';

// Re-export so existing importers (Primitives.test.tsx and others) keep
// working after the extraction to ./freshness (re-audit #4).
export { freshnessBucket, FRESHNESS_LABEL };
export type { FreshnessBucket };

/** Chip — `.chip` + `.chip--success/warning/danger/neutral` */
export function Chip({
  children,
  variant,
  icon,
  className,
  title,
  onRemove,
  removeLabel,
}: PropsWithChildren<{
  variant?: 'success' | 'warning' | 'danger' | 'neutral';
  icon?: IconName;
  className?: string;
  /**
   * Optional native `title` tooltip. Used by the "Refreshed …" chip to
   * surface the unambiguous ISO-UTC form on hover so operators in
   * different timezones can disambiguate a Slack screenshot. R5-19.
   */
  title?: string;
  /**
   * S8: dismiss affordance for removable filter chips (Lead Queue segment
   * intersection). The prototype chip block has no dismiss element, so
   * `.chip__remove` is a BEM extension of the prototype's `.chip`; the base
   * classes and typography stay prototype-exact.
   */
  onRemove?: () => void;
  /** Accessible name for the remove button, e.g. "Remove Investor filter". */
  removeLabel?: string;
}>) {
  const cls = ['chip', variant ? `chip--${variant}` : '', className ?? ''].filter(Boolean).join(' ');
  return (
    <span className={cls} title={title}>
      {icon && <Icon name={icon} size={10} />}
      <span className="chip__label">{children}</span>
      {onRemove && (
        <button
          type="button"
          className="chip__remove"
          aria-label={removeLabel ?? 'Remove filter'}
          onClick={onRemove}
        >
          <Icon name="cross" size={9} />
        </button>
      )}
    </span>
  );
}

/** Button — `.btn` + variants. Maps to prototype BEM. */
type ButtonVariant = 'primary' | 'ghost' | 'danger' | 'success' | 'default';
type ButtonSize = 'default' | 'sm';

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: IconName;
  iconEnd?: IconName;
  // React 19 passes `ref` as a normal prop; forwarding it lets callers
  // capture the underlying <button> (e.g. LeadTable focus restoration)
  // without wrapping the primitive in forwardRef.
  ref?: Ref<HTMLButtonElement>;
}

export function Button({ variant = 'default', size = 'default', icon, iconEnd, children, className, ...rest }: BtnProps) {
  const cls = [
    'btn',
    variant !== 'default' ? `btn--${variant}` : '',
    size === 'sm' ? 'btn--sm' : '',
    className ?? '',
  ].filter(Boolean).join(' ');
  return (
    <button className={cls} {...rest}>
      {icon && <Icon name={icon} size={14} />}
      {children}
      {iconEnd && <Icon name={iconEnd} size={14} />}
    </button>
  );
}

/** Evidence chip — `.evidence-chip`; clicking opens the DataSourceDrawer via context */
export function EvidenceChip({
  children,
  source,
  onClick,
  title,
}: PropsWithChildren<{ source?: DrawerSource; onClick?: () => void; title?: string }>) {
  const { setDrawer, showEvidence } = useApp();
  // Hooks must run before any early return; the hover card is a no-op
  // (returns null) when there is no source, so this is safe.
  const { anchorRef, anchorHandlers, hoverCard } = useEvidenceHoverCard(source);
  if (!showEvidence) return null;
  const handle = () => {
    if (onClick) onClick();
    else if (source) setDrawer(source);
  };
  // Re-audit #4 Buyer-Wow #8: the rich hover/focus micro-preview
  // (useEvidenceHoverCard) replaces the old native `title` auto-tooltip. We
  // still honor an explicit `title` override if a caller passed one; the
  // auto source-tooltip is gone (the card carries that info now).
  const fallbackTitle = title;
  // Visible freshness dot: no hover required. Missing updatedAt renders
  // no dot (NOT a grey placeholder, per design spec).
  const bucket = freshnessBucket(source?.updatedAt);
  return (
    <>
      <button
        type="button"
        className="evidence-chip"
        onClick={handle}
        title={fallbackTitle}
        ref={anchorRef}
        {...anchorHandlers}
      >
        <Icon name="link" size={9} className="e-ico" />
        <span className="evidence-chip__label">{children}</span>
        {bucket && (
          <span
            className={`evidence-chip__dot evidence-chip__dot--${bucket}`}
            role="img"
            aria-label={`${FRESHNESS_LABEL[bucket]} — refreshed ${source?.updatedAt ?? ''}`}
          />
        )}
      </button>
      {hoverCard}
    </>
  );
}
