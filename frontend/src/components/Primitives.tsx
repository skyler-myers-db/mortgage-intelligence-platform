import type { ButtonHTMLAttributes, PropsWithChildren } from 'react';
import { Icon, type IconName } from './Icon';
import { useApp, type DrawerSource } from './AppContext';

/** Chip — `.chip` + `.chip--success/warning/danger/neutral` */
export function Chip({
  children,
  variant,
  icon,
  className,
  title,
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
}>) {
  const cls = ['chip', variant ? `chip--${variant}` : '', className ?? ''].filter(Boolean).join(' ');
  return (
    <span className={cls} title={title}>
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

/**
 * Freshness buckets for an evidence source's `updatedAt`. The dot beside
 * the chip label lets operators eyeball data age without hovering:
 *   - fresh: updated within 7 days
 *   - aging: updated 7–30 days ago
 *   - stale: updated > 30 days ago
 *   - null:  no timestamp available; render no dot (not a grey placeholder)
 *
 * We parse the timestamp format used by DRAWER_SOURCES ("YYYY-MM-DD HH:MM
 * UTC"). `Date.parse` handles the ISO-ish form and the " UTC" suffix on
 * modern engines; when it doesn't, we coerce to ISO as a fallback.
 */
export type FreshnessBucket = 'fresh' | 'aging' | 'stale';

export function freshnessBucket(updatedAt?: string, now: Date = new Date()): FreshnessBucket | null {
  if (!updatedAt) return null;
  let ms = Date.parse(updatedAt);
  if (Number.isNaN(ms)) {
    // "2026-04-20 06:12 UTC" → "2026-04-20T06:12:00Z"
    const normalized = updatedAt.replace(' ', 'T').replace(/\s*UTC\s*$/i, 'Z');
    ms = Date.parse(normalized);
  }
  if (Number.isNaN(ms)) return null;
  const days = (now.getTime() - ms) / (1000 * 60 * 60 * 24);
  if (days <= 7) return 'fresh';
  if (days <= 30) return 'aging';
  return 'stale';
}

const FRESHNESS_LABEL: Record<FreshnessBucket, string> = {
  fresh: 'Fresh',
  aging: 'Aging',
  stale: 'Stale',
};

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
      : source.eventDate
        ? `Source: ${source.title} · Evidence event date ${source.eventDate}`
      : `Source: ${source.title}`
    : undefined;
  // Visible freshness dot: no hover required. Missing updatedAt renders
  // no dot (NOT a grey placeholder, per design spec).
  const bucket = freshnessBucket(source?.updatedAt);
  return (
    <button type="button" className="evidence-chip" onClick={handle} title={title ?? defaultTitle}>
      <Icon name="link" size={9} className="e-ico" />
      {children}
      {bucket && (
        <span
          className={`evidence-chip__dot evidence-chip__dot--${bucket}`}
          role="img"
          aria-label={`${FRESHNESS_LABEL[bucket]} — refreshed ${source?.updatedAt ?? ''}`}
          title={source?.updatedAt ? `Refreshed ${source.updatedAt}` : undefined}
        />
      )}
    </button>
  );
}
