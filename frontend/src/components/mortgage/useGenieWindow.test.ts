import { describe, expect, it } from 'vitest';
import {
  clampGeniePosition,
  fitGenieSizeToViewport,
  genieRightInset,
  snapGeniePosition,
} from './useGenieWindow';

describe('Genie window geometry', () => {
  it('keeps the preferred size when the viewport has room', () => {
    expect(fitGenieSizeToViewport(
      { w: 420, h: 640 },
      { w: 1_440, h: 900 },
    )).toEqual({ w: 420, h: 640 });
  });

  it('fits the panel inside the viewport gutter without changing the preference', () => {
    const preferred = { w: 420, h: 640 };

    expect(fitGenieSizeToViewport(preferred, { w: 390, h: 600 })).toEqual({
      w: 358,
      h: 568,
    });
    expect(preferred).toEqual({ w: 420, h: 640 });
  });

  it('clamps the full undocked panel inside the viewport gutter', () => {
    const viewport = { w: 800, h: 600 };

    expect(clampGeniePosition({ x: -100, y: 900 }, 420, 400, viewport)).toEqual({
      x: 16,
      y: 184,
    });
    expect(clampGeniePosition({ x: 999, y: -20 }, 420, 400, viewport)).toEqual({
      x: 364,
      y: 16,
    });
  });

  it('pins an oversized restored panel to the only available gutter position', () => {
    const viewport = { w: 320, h: 300 };
    const fitted = fitGenieSizeToViewport({ w: 420, h: 640 }, viewport);

    expect(clampGeniePosition({ x: 240, y: 180 }, fitted.w, fitted.h, viewport)).toEqual({
      x: 16,
      y: 16,
    });
  });

  it('snaps the three undocked corners and re-docks at bottom-right', () => {
    const viewport = { w: 800, h: 600 };

    expect(snapGeniePosition({ x: 10, y: 10 }, 420, 400, viewport)).toEqual({
      x: 16,
      y: 16,
    });
    expect(snapGeniePosition({ x: 370, y: 10 }, 420, 400, viewport)).toEqual({
      x: 364,
      y: 16,
    });
    expect(snapGeniePosition({ x: 10, y: 190 }, 420, 400, viewport)).toEqual({
      x: 16,
      y: 184,
    });
    expect(snapGeniePosition({ x: 370, y: 190 }, 420, 400, viewport)).toBeNull();
  });

  describe('with the Console open (audit responsive-v1 item 2)', () => {
    // The Console card: right 16, --console-w wide (300; 340 at >= 2560px).
    const cases = [
      { w: 1280, consoleW: 300 },
      { w: 1440, consoleW: 300 },
      { w: 2560, consoleW: 340 },
    ];

    it.each(cases)('keeps the panel clear of the Console at $w px', ({ w, consoleW }) => {
      const viewport = { w, h: 900 };
      const inset = genieRightInset(true, viewport, consoleW);
      expect(inset).toBe(consoleW + 32);
      const consoleLeft = w - 16 - consoleW;

      const clamped = clampGeniePosition({ x: w, y: 100 }, 420, 640, viewport, inset);
      // One gutter between the panel and the Console: the docked offset.
      expect(clamped.x + 420).toBe(consoleLeft - 16);
      expect(clamped.x + 420).toBe(w - inset);
      // A position left of the zone is kept as the user chose it.
      expect(clampGeniePosition({ x: 200, y: 100 }, 420, 640, viewport, inset)).toEqual({ x: 200, y: 100 });
    });

    it.each(cases)('snaps the top-right corner to the Console edge and re-docks at its bottom-right at $w px', ({ w, consoleW }) => {
      const viewport = { w, h: 900 };
      const inset = genieRightInset(true, viewport, consoleW);
      const consoleLeft = w - 16 - consoleW;

      const topRight = snapGeniePosition({ x: consoleLeft - 16 - 420 - 5, y: 10 }, 420, 400, viewport, inset);
      expect(topRight).toEqual({ x: consoleLeft - 16 - 420, y: 16 });
      expect(snapGeniePosition({ x: consoleLeft - 16 - 420 - 5, y: 490 }, 420, 400, viewport, inset)).toBeNull();
      // Far from the Console's edge nothing snaps.
      expect(snapGeniePosition({ x: 200, y: 200 }, 420, 400, viewport, inset)).toEqual({ x: 200, y: 200 });
    });

    it('takes no inset while the Console is closed, or a bottom sheet below 1280px', () => {
      expect(genieRightInset(false, { w: 1440, h: 900 }, 300)).toBe(0);
      expect(genieRightInset(true, { w: 1279, h: 900 }, 300)).toBe(0);
      expect(clampGeniePosition({ x: 1440, y: 100 }, 420, 640, { w: 1440, h: 900 }, 0)).toEqual({ x: 1004, y: 100 });
    });
  });

  it('leaves a position unchanged away from viewport corners', () => {
    expect(snapGeniePosition(
      { x: 100, y: 100 },
      420,
      400,
      { w: 1_200, h: 900 },
    )).toEqual({ x: 100, y: 100 });
  });
});
