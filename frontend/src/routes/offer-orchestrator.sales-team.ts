import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import type { SalesTeamMember } from '../types';

/** Load active reviewers for optional assignment without gating approval. */
export function useOfferSalesTeam(): SalesTeamMember[] {
  const [salesTeam, setSalesTeam] = useState<SalesTeamMember[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .salesTeam()
      .then((members) => {
        if (cancelled) return;
        setSalesTeam(
          members.filter((member) => (
            member.active
            && (member.role === 'loan_officer' || member.role === 'sales_manager')
          )),
        );
      })
      .catch(() => {
        // Assignment is optional; approval remains available when the roster is down.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return salesTeam;
}
