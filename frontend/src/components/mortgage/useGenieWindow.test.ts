import { describe, expect, it } from 'vitest';
import {
  clampGeniePosition,
  fitGenieSizeToViewport,
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

  it('leaves a position unchanged away from viewport corners', () => {
    expect(snapGeniePosition(
      { x: 100, y: 100 },
      420,
      400,
      { w: 1_200, h: 900 },
    )).toEqual({ x: 100, y: 100 });
  });
});
