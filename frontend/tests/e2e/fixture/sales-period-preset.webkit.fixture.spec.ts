/**
 * The Sales ops period preset pressed over a dirty custom draft, in WebKit
 * (W5e C11 residual 2). Collected only by the fixture-webkit project
 * (MIP_CROSS_ENGINE=1); the Chromium twin is case (f) in
 * sales-loop.fixture.spec.ts, and both run salesPeriodPreset.ts.
 *
 * WebKit is where the two-history-entries defect lived: it never focuses a
 * pressed button, so without the preset's own mousedown handling the press
 * blurred the date field with no relatedTarget and applied the draft before
 * the click wrote the preset.
 */
import { expectPresetPressOverDirtyDraft } from './salesPeriodPreset';
import { test } from './test';

test('a preset pressed over a dirty draft writes one history entry and keeps focus on the preset', async ({ app, page }) => {
  await expectPresetPressOverDirtyDraft(app, page);
});
