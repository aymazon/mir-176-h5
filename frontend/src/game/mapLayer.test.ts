import { describe, expect, it } from 'vitest';

import { TILE_SIZE, cellFromScreenDelta } from './mapLayer';

describe('cellFromScreenDelta', () => {
  it('converts a 3-tile-left screen delta to the adjacent cell', () => {
    expect(cellFromScreenDelta(150, 150, -3 * TILE_SIZE, 0)).toEqual({ x: 147, y: 150 });
  });

  it('rounds the fractional sum per axis', () => {
    expect(cellFromScreenDelta(150, 150, TILE_SIZE * 2.5, 0)).toEqual({ x: 153, y: 150 });
    expect(cellFromScreenDelta(150, 150, 0, TILE_SIZE * 2)).toEqual({ x: 150, y: 152 });
    expect(cellFromScreenDelta(150, 150, -31, 0)).toEqual({ x: 149, y: 150 });
  });

  it('keeps the character cell for sub-half-tile deltas', () => {
    expect(cellFromScreenDelta(150, 150, 10, 10)).toEqual({ x: 150, y: 150 });
  });
});
