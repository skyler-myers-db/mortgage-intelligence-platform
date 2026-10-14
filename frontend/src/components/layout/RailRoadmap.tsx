import { Icon, type IconName } from '../Icon';
import { Tooltip } from '../ui/Tooltip';

/**
 * The M1-M4 roadmap rail slots, shown only in presenter mode
 * (deviation:rail-roadmap-presenter-only; critic-05, shell-09;
 * D-shell-deviations-e2). The prototype rail lists all five modules
 * (design_files/Module 0 Prototype.html:1180-1204); a customer workspace
 * shows only what it can open, so the roadmap is a demo affordance. Rail
 * loads this module lazily, and only while presenter mode is on, so a
 * customer never downloads the roadmap copy.
 *
 * Each slot is a focusable `<button aria-disabled="true">` with no action:
 * its accessible name is the visible "M{id}" (WCAG 2.5.3) and the Tooltip
 * primitive's persistent hidden description is its aria-describedby, so the
 * roadmap note is read once, on focus as well as hover. No DOM title=
 * (critic-08).
 */

interface RoadmapModule {
  id: number;
  name: string;
  icon: IconName;
  desc: string;
}

const ROADMAP_MODULES: readonly RoadmapModule[] = [
  { id: 1, name: 'Pipeline Optimization', icon: 'flow', desc: 'Lead → app → approval throughput and stalls.' },
  { id: 2, name: 'LO Workbench', icon: 'money', desc: 'Officer assist with explainable borrower guidance.' },
  { id: 3, name: 'Underwriting Copilot', icon: 'shield', desc: 'Condition handling and exception triage.' },
  { id: 4, name: 'Risk & Retention', icon: 'audit', desc: 'Portfolio-level retention and recapture.' },
];

/** The roadmap note a slot describes itself with. */
function roadmapDescription(module: RoadmapModule): string {
  return `Module ${module.id}: ${module.name}. ${module.desc} On the roadmap; not part of this workspace.`;
}

export default function RailRoadmap() {
  return (
    <>
      {ROADMAP_MODULES.map((module) => (
        <Tooltip key={module.id} content={roadmapDescription(module)}>
          <button type="button" className="rail__item rail__item--disabled" aria-disabled="true">
            <Icon name={module.icon} size={18} className="ico" />
            <span className="mod">M{module.id}</span>
          </button>
        </Tooltip>
      ))}
    </>
  );
}
