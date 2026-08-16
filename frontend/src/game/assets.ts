// D8 procedural placeholder art: canvas-generated floor tiles and class sprites,
// behind a stable loadAtlas() interface that W2 replaces with real art assets.

import { CanvasSource, Texture } from 'pixi.js';

import type { CharacterClassKind } from '../api/rest';

export const SPRITE_SIZE = 32;

export type SpriteDirection = 'up' | 'down' | 'left' | 'right';

/** Neutral class id for other players (the W1 entity wire format carries no class). */
export const OTHER_PLAYER_CLASS = 'other';

export interface WorldAtlas {
  readonly floorA: Texture;
  readonly floorB: Texture;
  /** Keyed by class id (warrior/mage/taoist/other) → direction → texture. */
  readonly sprites: ReadonlyMap<string, ReadonlyMap<SpriteDirection, Texture>>;
}

// World-art colors mirror the DESIGN.md §2 tokens (--floor-a/--floor-b/
// --sprite-* / --class-*); canvas 2D has no access to CSS custom properties.
const FLOOR_A = '#2b221a';
const FLOOR_B = '#251d16';
const FLOOR_EDGE = 'rgba(0, 0, 0, 0.22)';
const FLOOR_LIGHT = 'rgba(255, 255, 255, 0.03)';
const SPRITE_SHADOW = 'rgba(0, 0, 0, 0.35)';
const SPRITE_SKIN = '#e8c39e';
const SPRITE_FACE = '#1a120a';
const CLASS_COLORS: Record<CharacterClassKind, string> = {
  warrior: '#c0392b',
  mage: '#2980ff',
  taoist: '#27ae60',
};
const OTHER_COLOR = '#b08d57';

// Two-frame float: 125ms per frame, 250ms full period, y offset 0 → -2px.
export const FLOAT_FRAME_MS = 125;
export const FLOAT_OFFSETS_Y: readonly number[] = [0, -2];

export function floatFrameIndex(nowMs: number): 0 | 1 {
  return (Math.max(0, Math.floor(nowMs / FLOAT_FRAME_MS)) % 2) as 0 | 1;
}

export function floatOffsetY(nowMs: number): number {
  return FLOAT_OFFSETS_Y[floatFrameIndex(nowMs)];
}

function makeCanvas(size: number): { canvas: HTMLCanvasElement; ctx: CanvasRenderingContext2D } {
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext('2d');
  if (context === null) throw new Error('2D canvas context unavailable');
  return { canvas, ctx: context };
}

function toTexture(canvas: HTMLCanvasElement): Texture {
  // Explicit size/resolution: a bare CanvasSource(canvas) drops the art
  // (no options object -> resource replaced, width/NaN -> 0x0 canvas).
  return new Texture(
    new CanvasSource({
      resource: canvas,
      width: canvas.width,
      height: canvas.height,
      resolution: 1,
      autoDensity: true,
    }),
  );
}

function mixToward(hex: string, target: string, amount: number): string {
  const parse = (value: string): [number, number, number] => [
    parseInt(value.slice(1, 3), 16),
    parseInt(value.slice(3, 5), 16),
    parseInt(value.slice(5, 7), 16),
  ];
  const [r1, g1, b1] = parse(hex);
  const [r2, g2, b2] = parse(target);
  const mix = (a: number, b: number): string =>
    Math.round(a + (b - a) * amount).toString(16).padStart(2, '0');
  return `#${mix(r1, r2)}${mix(g1, g2)}${mix(b1, b2)}`;
}

function buildFloorTile(shade: string): Texture {
  const { canvas, ctx } = makeCanvas(SPRITE_SIZE);
  ctx.fillStyle = shade;
  ctx.fillRect(0, 0, SPRITE_SIZE, SPRITE_SIZE);
  ctx.fillStyle = FLOOR_EDGE;
  ctx.fillRect(0, SPRITE_SIZE - 1, SPRITE_SIZE, 1);
  ctx.fillRect(SPRITE_SIZE - 1, 0, 1, SPRITE_SIZE);
  ctx.fillStyle = FLOOR_LIGHT;
  ctx.fillRect(0, 0, SPRITE_SIZE, 1);
  ctx.fillRect(0, 0, 1, SPRITE_SIZE);
  return toTexture(canvas);
}

/** Simple directional body: shadow + class-color torso + head with eyes facing the direction. */
function buildBodySprite(color: string, direction: SpriteDirection): Texture {
  const { canvas, ctx } = makeCanvas(SPRITE_SIZE);
  const cx = SPRITE_SIZE / 2;

  ctx.fillStyle = SPRITE_SHADOW;
  ctx.beginPath();
  ctx.ellipse(cx, 29, 9, 3, 0, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = color;
  ctx.fillRect(9, 13, 14, 14);
  ctx.fillStyle = mixToward(color, '#000000', 0.35);
  ctx.fillRect(9, 26, 14, 1);

  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(cx, 6, 7, Math.PI, 0);
  ctx.fill();

  ctx.fillStyle = SPRITE_SKIN;
  ctx.beginPath();
  ctx.arc(cx, 8, 6, 0, Math.PI * 2);
  ctx.fill();

  if (direction !== 'up') {
    ctx.fillStyle = SPRITE_FACE;
    const shift = direction === 'left' ? -2.5 : direction === 'right' ? 2.5 : 0;
    const drop = direction === 'down' ? 2 : 0;
    ctx.fillRect(13 + shift, 7.5 + drop, 2, 2.5);
    ctx.fillRect(17 + shift, 7.5 + drop, 2, 2.5);
  }
  return toTexture(canvas);
}

function buildAtlas(): WorldAtlas {
  const sprites = new Map<string, ReadonlyMap<SpriteDirection, Texture>>();
  const directions: readonly SpriteDirection[] = ['up', 'down', 'left', 'right'];
  for (const characterClass of Object.keys(CLASS_COLORS) as CharacterClassKind[]) {
    const color = CLASS_COLORS[characterClass];
    sprites.set(
      characterClass,
      new Map(directions.map((direction) => [direction, buildBodySprite(color, direction)])),
    );
  }
  // Other players: single facing-down neutral body (no direction on the wire).
  sprites.set(OTHER_PLAYER_CLASS, new Map([['down', buildBodySprite(OTHER_COLOR, 'down')]]));
  return {
    floorA: buildFloorTile(FLOOR_A),
    floorB: buildFloorTile(FLOOR_B),
    sprites,
  };
}

/**
 * Atlas loader. D8: fully procedural. W2 swaps the body for real sprite
 * sheets and must keep this shape so world/spriteFactory code is untouched.
 */
export function loadAtlas(): Promise<WorldAtlas> {
  return Promise.resolve(buildAtlas());
}
