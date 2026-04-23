import type { ButtonHTMLAttributes, PropsWithChildren } from 'react';
import { Icon, type IconName } from './Icon';
import { useApp, type DrawerSource } from './AppContext';

/** Chip — `.chip` + `.chip--success/warning/danger/neutral` */
export function Chip({
  children,
  variant,
  icon,
  className,
}: PropsWithChildren<{ variant?: 'success' | 'warning' | 'danger' | 'neutral'; icon?: IconName; className?: string }>) {
  const cls = ['chip', variant ? `chip--${variant}` : '', className ?? ''].filter(Boolean).join(' ');
  return (
    <span className={cls}>
      {icon && <Icon name={icon} size={10} />}
      {children}
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
  if (!showEvidence) return null;
  const handle = () => {
    if (onClick) onClick();
    else if (source) setDrawer(source);
  };
  // Default tooltip: Source title + refresh timestamp when present.
  // Marketing Leaders asked for "when was this data last refreshed?" in
  // the LO walk 2026-04-22; surfacing updatedAt via native title keeps
  // the chip visually uncluttered.
  const defaultTitle = source
    ? source.updatedAt
      ? `Source: ${source.title} · Refreshed ${source.updatedAt}`
      : `Source: ${source.title}`
    : undefined;
  return (
    <button type="button" className="evidence-chip" onClick={handle} title={title ?? defaultTitle}>
      <Icon name="link" size={9} className="e-ico" />
      {children}
    </button>
  );
}
