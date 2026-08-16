import { describe, expect, it } from 'vitest';

import { FLOAT_FRAME_MS, FLOAT_OFFSETS_Y, floatFrameIndex, floatOffsetY } from './assets';

describe('float animation data', () => {
  it('steps through two frames with a 250ms full period', () => {
    expect(floatFrameIndex(0)).toBe(0);
    expect(floatFrameIndex(FLOAT_FRAME_MS - 1)).toBe(0);
    expect(floatFrameIndex(FLOAT_FRAME_MS)).toBe(1);
    expect(floatFrameIndex(FLOAT_FRAME_MS * 2)).toBe(0);
    expect(floatFrameIndex(FLOAT_FRAME_MS * 3 + 10)).toBe(1);
  });

  it('yields a ±0/-2px offset per frame', () => {
    expect(floatOffsetY(0)).toBe(0);
    expect(floatOffsetY(FLOAT_FRAME_MS)).toBe(-2);
    expect(FLOAT_OFFSETS_Y).toEqual([0, -2]);
  });
});
