import type { LeadSummary } from '../../types';
import { Chip } from '../Primitives';

type TruthFlag = {
  label: string;
  active: boolean;
  activeLabel: string;
  inactiveLabel: string;
  activeVariant?: 'success' | 'warning' | 'danger' | 'neutral';
};

type TruthFlagView = {
  label: string;
  variant: 'success' | 'warning' | 'danger' | 'neutral';
};

/**
 * Owner-resolution caveats (S1.1). Unlike truth flags, these only render when
 * the caveat is active — there is no inactive counterpart chip. Display-only;
 * suppression of unresolved owners is enforced in the gold/backend layer.
 */
export function ownerCaveatLabels(borrower: LeadSummary): TruthFlagView[] {
  const caveats: TruthFlagView[] = [];
  const ownerCount = Number(borrower.owner_count ?? 1);
  if (ownerCount > 1) {
    caveats.push({ label: `Multi-owner (${ownerCount})`, variant: 'neutral' });
  }
  if (borrower.primary_owner_entity_type === 'trust') {
    caveats.push({ label: 'Trust-held', variant: 'neutral' });
  }
  if (borrower.primary_owner_entity_type === 'llc') {
    caveats.push({ label: 'LLC/entity-held', variant: 'neutral' });
  }
  if (borrower.has_unresolved_owner === true) {
    caveats.push({ label: 'Owner unresolved — outreach suppressed', variant: 'warning' });
  }
  return caveats;
}

export function truthFlagLabels(borrower: LeadSummary, compact = false): TruthFlagView[] {
  const hasAbsenteeSignal = 'is_absentee' in borrower;
  const hasCorporateSignal = 'is_corporate_owner' in borrower;
  const hasFiledPermit = borrower.has_permit === true;
  const hasHelocIntent = hasFiledPermit || borrower.has_heloc_propensity_trigger === true;
  const flags: TruthFlag[] = [
    {
      label: 'current customer',
      active: borrower.is_current_customer === true,
      activeLabel: 'Current customer',
      inactiveLabel: 'Not current customer',
      activeVariant: 'success',
    },
    {
      label: 'former customer',
      active: borrower.is_former_customer === true,
      activeLabel: 'Former customer',
      inactiveLabel: 'No former-customer signal',
      activeVariant: 'neutral',
    },
    {
      label: 'competitor lien',
      active: borrower.is_competitor_lien === true,
      activeLabel: 'Competitor lien',
      inactiveLabel: 'No competitor lien',
      activeVariant: 'warning',
    },
    {
      label: 'occupancy',
      active: borrower.is_owner_occupied === true,
      activeLabel: 'Owner occupied',
      inactiveLabel: 'Non-owner occupied',
      activeVariant: 'success',
    },
    {
      label: 'investor',
      active: borrower.is_investor === true,
      activeLabel: 'Investor',
      inactiveLabel: 'Not investor',
      activeVariant: 'warning',
    },
    ...(hasAbsenteeSignal
      ? [
          {
            label: 'absentee',
            active: (borrower as LeadSummary & { is_absentee?: boolean }).is_absentee === true,
            activeLabel: 'Absentee owner',
            inactiveLabel: 'Not absentee',
            activeVariant: 'warning' as const,
          },
        ]
      : []),
    ...(hasCorporateSignal
      ? [
          {
            label: 'corporate owner',
            active: (borrower as LeadSummary & { is_corporate_owner?: boolean }).is_corporate_owner === true,
            activeLabel: 'Corporate owner',
            inactiveLabel: 'Individual owner',
            activeVariant: 'warning' as const,
          },
        ]
      : []),
    {
      label: 'listing',
      active: borrower.listed_for_sale === true,
      activeLabel: 'Listed for sale',
      inactiveLabel: 'No listing trigger',
      activeVariant: 'warning',
    },
    {
      label: 'heloc intent',
      active: hasHelocIntent,
      activeLabel: hasFiledPermit ? 'Permit activity' : 'HELOC intent',
      inactiveLabel: 'No HELOC intent',
      activeVariant: 'warning',
    },
    {
      label: 'second lien',
      active: Number(borrower.second_pos_amount ?? 0) > 0,
      activeLabel: 'Open 2nd lien',
      inactiveLabel: 'No 2nd lien',
      activeVariant: 'neutral',
    },
  ];

  return flags.slice(0, compact ? 5 : flags.length).map((flag) => ({
    label: flag.active ? flag.activeLabel : flag.inactiveLabel,
    variant: flag.active ? flag.activeVariant ?? 'success' : 'neutral',
  }));
}

export function BorrowerTruthFlags({ borrower, compact = false }: { borrower: LeadSummary; compact?: boolean }) {
  const flags = truthFlagLabels(borrower, compact);
  const caveats = ownerCaveatLabels(borrower);
  return (
    <div className="chip-row">
      {caveats.map((caveat) => (
        <Chip key={caveat.label} variant={caveat.variant}>
          {caveat.label}
        </Chip>
      ))}
      {flags.map((flag) => (
        <Chip
          key={flag.label}
          variant={flag.variant}
        >
          {flag.label}
        </Chip>
      ))}
    </div>
  );
}
