// Player sprites: self (class-colored, directional) and other players
// (neutral body + name label above the head).

import { Container, Sprite, Text } from 'pixi.js';

import {
  OTHER_PLAYER_CLASS,
  SPRITE_SIZE,
  type SpriteDirection,
  type WorldAtlas,
} from './assets';

export function createPlayerSprite(
  atlas: WorldAtlas,
  classId: string,
  direction: SpriteDirection = 'down',
): Sprite {
  const classTextures = atlas.sprites.get(classId) ?? atlas.sprites.get(OTHER_PLAYER_CLASS);
  const texture = classTextures?.get(direction) ?? classTextures?.get('down') ?? atlas.floorA;
  return new Sprite(texture);
}

export function createEntitySprite(atlas: WorldAtlas, name: string): Container {
  const container = new Container();
  const body = createPlayerSprite(atlas, OTHER_PLAYER_CLASS, 'down');
  body.anchor.set(0.5, 0.5);
  container.addChild(body);
  const label = new Text({
    text: name,
    style: {
      fontFamily: 'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif',
      fontSize: 12,
      fill: 0xffffff,
      stroke: { color: 0x000000, width: 2 },
    },
  });
  label.anchor.set(0.5, 1);
  label.position.set(0, -SPRITE_SIZE / 2 - 2);
  container.addChild(label);
  return container;
}

/** The name label is always the second child of an entity container. */
export function updateEntityLabel(container: Container, name: string): void {
  const label = container.getChildAt(1);
  if (label instanceof Text && label.text !== name) label.text = name;
}
