/**
 * Sparkle means Genie (2026-09-21 audit critic-12 remainder). The prototype
 * draws `sparkle` only for Genie: the topbar Ask Genie button, the Genie
 * panel, its suggestion chips and the activity log's genie rows
 * (design_files/Module 0 Prototype.html:1238, :1365, :1402, :1512). The app
 * had spread it to deterministic copy (the Home briefing, the dossier story,
 * two Regenerate buttons), so it no longer signalled model output.
 *
 * Reads source: the non-test files under frontend/src that spell a
 * `'sparkle'` / `"sparkle"` icon literal are a subset of the Genie and
 * model-output surfaces listed here. A lane that adds a Genie surface extends
 * the list; a listed file that drops the glyph is harmless.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads source text under Vitest only.
import { readdirSync, readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

declare const process: { cwd(): string };

/** Genie and model-output surfaces, relative to frontend/src. */
const GENIE_SURFACE_FILES = [
  'components/Icon.tsx', // the IconName union that defines the glyph
  'components/command/CommandPaletteDialog.tsx', // Ask Genie rows
  'components/command/commandActions.ts', // cmd-genie
  'components/layout/GenieDock.tsx',
  'components/layout/ShellPanelBoundaries.tsx', // the Genie FAB fallback
  'components/layout/Topbar.tsx', // the Genie toggle
  'components/mortgage/GenieAnswer.tsx',
  'components/mortgage/GenieAskAbout.tsx',
  'components/mortgage/GenieChat.tsx',
  'components/mortgage/GenieChatBody.tsx',
  'components/mortgage/GenieConversationLinkState.tsx',
  'components/mortgage/GenieProgress.tsx',
  'lib/auditEventPresentation.ts', // GENIE activity events (moved from components/layout/Console.tsx)
  'lib/routeMeta.ts', // askGenie
  'routes/analytics.tsx', // the Ask Genie link
  'routes/ask-genie.answer-panel.tsx',
  'routes/ask-genie.compose-plan-card.tsx', // the Growth Agent co-pilot
  'routes/ask-genie.growth-agent-panel.tsx',
  'routes/ask-genie.growth-run-card.tsx',
  'routes/home.tsx', // the Ask Genie link
  'routes/offer-orchestrator.panels.tsx', // a supervisor-generated draft only (below)
];

const SPARKLE = /(['"])sparkle\1/g;
const MESSAGE = 'sparkle is reserved for Genie and model output (critic-12)';

function sourceFiles(): string[] {
  const entries: string[] = readdirSync(join(process.cwd(), 'src'), { recursive: true });
  return entries
    .map((entry) => entry.split('\\').join('/'))
    .filter((entry) => /\.(ts|tsx)$/.test(entry) && !/\.test\.tsx?$/.test(entry) && !entry.startsWith('test/'))
    .sort();
}

const read = (file: string) => readFileSync(join(process.cwd(), 'src', file), 'utf8') as string;

describe('sparkle marks Genie and model output only', () => {
  it('appears only in the listed Genie surfaces', () => {
    const offenders = sourceFiles().filter((file) => (read(file).match(SPARKLE) ?? []).length > 0 && !GENIE_SURFACE_FILES.includes(file));
    expect(offenders, MESSAGE).toEqual([]);
  });

  it('keeps the Offer Orchestrator sparkle to a fresh supervisor-generated draft', () => {
    const panels = read('routes/offer-orchestrator.panels.tsx');
    expect(panels.match(SPARKLE) ?? [], MESSAGE).toHaveLength(1);
    expect(panels).toMatch(/draftGenerationMode === 'supervisor' \? 'sparkle' : 'doc'/);
  });

  it('draws the deterministic briefing, story and Regenerate controls without it', () => {
    for (const file of [
      'components/mortgage/HomeAnswerBand.tsx',
      'components/mortgage/BorrowerStoryCard.tsx',
      'routes/portfolio-builder.campaign-setup.tsx',
    ]) {
      expect(read(file).match(SPARKLE) ?? [], `${file}: ${MESSAGE}`).toEqual([]);
    }
  });
});
