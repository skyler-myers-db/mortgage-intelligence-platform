import type { CampaignSetupState } from './portfolio-builder.logic';

/**
 * What Portfolio Builder counts as unsaved work for the navigation guard
 * (2026-09-21 audit states-05): filter changes the operator has not run
 * (`buildDirty`), and a campaign setup that differs from the one last saved
 * with a build (or from the defaults before any save).
 *
 * The setup survives an in-app navigation and a reload as this tab's draft
 * (critic-v3, portfolio-builder.draft.ts), so its copy says so; the guard
 * stays, because closing the tab still loses a session draft.
 */

/** Field-by-field equality; every CampaignSetupState field is a primitive. */
export function campaignSetupsEqual(a: CampaignSetupState, b: CampaignSetupState): boolean {
  return (Object.keys(a) as Array<keyof CampaignSetupState>).every((key) => a[key] === b[key]);
}

/** The "Leave without saving?" message for what is actually unsaved, or null when nothing is. */
export function portfolioUnsavedMessage(filtersNotRun: boolean, setupNotSaved: boolean): string | null {
  if (filtersNotRun && setupNotSaved) {
    return 'Your filter changes have not been run and will be discarded. Your campaign setup has not been saved with a build; it is kept as a draft in this tab until you save or reset it.';
  }
  if (filtersNotRun) return 'Your filter changes have not been run. Leaving discards them.';
  if (setupNotSaved) {
    return 'Your campaign setup has not been saved with a build. It is kept as a draft in this tab until you save or reset it.';
  }
  return null;
}
