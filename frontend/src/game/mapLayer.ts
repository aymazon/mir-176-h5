// Viewport-culled 32px procedural floor. The W1 protocol carries no wall or
// tilemap data — the server is the path authority; this layer only paints ground.

import { Container, Sprite } from 'pixi.js';

import type { WorldAtlas } from './assets';

export const TILE_SIZE = 32;

/** Click target cell: character cell + screen delta / tile size, rounded. */
export function cellFromScreenDelta(
  charCellX: number,
  charCellY: number,
  screenDeltaX: number,
  screenDeltaY: number,
): { x: number; y: number } {
  return {
    x: Math.round(charCellX + screenDeltaX / TILE_SIZE),
    y: Math.round(charCellY + screenDeltaY / TILE_SIZE),
  };
}

// ±8 cols × ±12 rows (17×25 = 425 tiles) covers the 375×667 viewport at 32px.
const HALF_COLS = 8;
const HALF_ROWS = 12;
const COLS = HALF_COLS * 2 + 1;
const ROWS = HALF_ROWS * 2 + 1;

export class MapLayer extends Container {
  private readonly tiles: Sprite[] = [];
  private readonly floorA: Sprite['texture'];
  private readonly floorB: Sprite['texture'];
  private renderedKey = '';

  constructor(atlas: WorldAtlas) {
    super();
    this.floorA = atlas.floorA;
    this.floorB = atlas.floorB;
    for (let i = 0; i < COLS * ROWS; i++) {
      const tile = new Sprite(atlas.floorA);
      this.tiles.push(tile);
      this.addChild(tile);
    }
  }

  /** Reposition the pooled tiles around the character's world-pixel center. */
  update(centerX: number, centerY: number): void {
    const leftTile = Math.round(centerX / TILE_SIZE) - HALF_COLS;
    const topTile = Math.round(centerY / TILE_SIZE) - HALF_ROWS;
    const key = `${leftTile}:${topTile}`;
    if (key === this.renderedKey) return;
    this.renderedKey = key;
    let index = 0;
    for (let row = 0; row < ROWS; row++) {
      for (let col = 0; col < COLS; col++) {
        const tile = this.tiles[index++];
        const tileX = leftTile + col;
        const tileY = topTile + row;
        tile.texture = ((tileX + tileY) % 2 + 2) % 2 === 0 ? this.floorA : this.floorB;
        tile.position.set(tileX * TILE_SIZE, tileY * TILE_SIZE);
      }
    }
  }
}
