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

export function truthFlagLabels(borrower: LeadSummary, compact = false): TruthFlagView[] {
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
    {
      label: 'listing',
      active: borrower.listed_for_sale === true,
      activeLabel: 'Listed for sale',
      inactiveLabel: 'Listing feed pending',
      activeVariant: 'warning',
    },
    {
      label: 'permit',
      active: borrower.has_permit === true,
      activeLabel: 'Permit activity',
      inactiveLabel: 'Permit feed pending',
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
  return (
    <div className="chip-row">
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
