import type { GrowthAgentSchedulerState } from '../types/growthAgent';

/**
 * deviation:growth-agent-scheduled-run-status: each row says whether scheduled
 * runs are on, from the scheduler job's real state (audit 2026-09-21
 * flow-08 slice 2), instead of a hard-coded paused string.
 */
export const SCHEDULED_RUN_STATUS: Record<GrowthAgentSchedulerState, string> = {
  active: 'scheduled runs on',
  paused: 'scheduled runs off',
  unavailable: 'scheduled-run status unavailable',
};
