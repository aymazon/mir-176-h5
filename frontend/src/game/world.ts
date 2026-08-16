// PIXI world: WebGL renderer with canvas fallback (headless e2e has no GPU),
// a camera centered on the interpolated character position, and a
// requestAnimationFrame loop that reads ViewStore with performance.now().

import { Application, Container, Sprite } from 'pixi.js';

import type { ViewStore } from '../store/viewStore';

import {
  loadAtlas,
  floatOffsetY,
  type SpriteDirection,
  type WorldAtlas,
} from './assets';
import { EffectLayer } from './effectLayer';
import { MapLayer, TILE_SIZE } from './mapLayer';
import { createEntitySprite, createPlayerSprite, updateEntityLabel } from './spriteFactory';

export interface WorldOptions {
  readonly container: HTMLElement;
  readonly viewStore: ViewStore;
}

const BACKGROUND = 0x0c0906; // --color-well

async function createApplication(container: HTMLElement): Promise<Application> {
  const base = {
    backgroundColor: BACKGROUND,
    resizeTo: container,
    autoStart: false,
    antialias: true,
  };
  const app = new Application();
  try {
    await app.init({ ...base, preference: ['webgl', 'canvas'] });
    return app;
  } catch {
    try {
      app.destroy();
    } catch {
      // failed init may leave nothing to destroy
    }
    const fallback = new Application();
    await fallback.init({ ...base, preference: ['canvas'] });
    return fallback;
  }
}

export class World {
  private readonly options: WorldOptions;
  private app: Application | null = null;
  private atlas: WorldAtlas | null = null;
  private readonly camera = new Container();
  private readonly entityLayer = new Container();
  private readonly effectLayer = new EffectLayer();
  private mapLayer: MapLayer | null = null;
  private selfSprite: Sprite | null = null;
  private selfClass = '';
  private pendingSelfClass: string | null = null;
  private readonly entitySprites = new Map<number, Container>();
  private rafId: number | null = null;
  private disposed = false;
  private reducedMotion = false;

  constructor(options: WorldOptions) {
    this.options = options;
  }

  async start(): Promise<void> {
    const atlas = await loadAtlas();
    if (this.disposed) return;
    const app = await createApplication(this.options.container);
    if (this.disposed) {
      app.destroy(true, true);
      return;
    }
    this.app = app;
    this.atlas = atlas;
    this.reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    this.mapLayer = new MapLayer(atlas);
    this.camera.addChild(this.mapLayer, this.entityLayer, this.effectLayer);
    app.stage.addChild(this.camera);
    this.options.container.appendChild(app.canvas);

    if (this.pendingSelfClass !== null) this.applySelfSprite(this.pendingSelfClass);

    const loop = (): void => {
      if (this.disposed) return;
      this.rafId = window.requestAnimationFrame(loop);
      const nowMs = performance.now();
      this.updateWorld(nowMs);
      app.render();
    };
    this.rafId = window.requestAnimationFrame(loop);
  }

  /** Set the self sprite for the active character (called on char_ready). */
  setSelfCharacter(classId: string): void {
    if (this.disposed) return;
    if (this.atlas === null) {
      this.pendingSelfClass = classId;
      return;
    }
    this.applySelfSprite(classId);
  }

  setSelfDirection(direction: SpriteDirection): void {
    const selfSprite = this.selfSprite;
    const atlas = this.atlas;
    if (selfSprite === null || atlas === null) return;
    const classTextures = atlas.sprites.get(this.selfClass);
    const texture = classTextures?.get(direction);
    if (texture !== undefined) selfSprite.texture = texture;
  }

  stop(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.rafId !== null) {
      window.cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    const app = this.app;
    this.app = null;
    if (app !== null) {
      try {
        app.destroy(true, true);
      } catch {
        // destroy is idempotent; a torn-down renderer must not block cleanup
      }
    }
    this.entitySprites.clear();
    this.selfSprite = null;
    this.mapLayer = null;
    this.atlas = null;
    this.pendingSelfClass = null;
  }

  private applySelfSprite(classId: string): void {
    this.pendingSelfClass = null;
    this.selfClass = classId;
    const previous = this.selfSprite;
    if (previous !== null) {
      this.camera.removeChild(previous);
      previous.destroy();
    }
    const sprite = createPlayerSprite(this.atlas as WorldAtlas, classId, 'down');
    sprite.anchor.set(0.5, 0.5);
    this.selfSprite = sprite;
    this.camera.addChildAt(sprite, this.camera.getChildIndex(this.effectLayer));
  }

  private updateWorld(nowMs: number): void {
    const viewStore = this.options.viewStore;
    const character = viewStore.character;
    if (character === undefined) return;
    const position = viewStore.characterPositionAt(nowMs);
    if (position === undefined) return;
    const app = this.app;
    if (app === null) return;

    const px = position.x * TILE_SIZE + TILE_SIZE / 2;
    const py = position.y * TILE_SIZE + TILE_SIZE / 2;
    this.camera.x = app.screen.width / 2 - px;
    this.camera.y = app.screen.height / 2 - py;
    this.mapLayer?.update(px, py);

    const floatY = this.reducedMotion ? 0 : floatOffsetY(nowMs);
    if (this.selfSprite !== null) this.selfSprite.position.set(px, py + floatY);

    this.syncEntities(viewStore);
  }

  private syncEntities(viewStore: ViewStore): void {
    const atlas = this.atlas;
    if (atlas === null) return;
    const character = viewStore.character;
    const nowMs = performance.now();
    const seen = new Set<number>();
    for (const entity of viewStore.visibleEntities(nowMs)) {
      if (character !== undefined && entity.id === character.id) continue;
      seen.add(entity.id);
      let container = this.entitySprites.get(entity.id);
      if (container === undefined) {
        container = createEntitySprite(atlas, entity.name);
        this.entityLayer.addChild(container);
        this.entitySprites.set(entity.id, container);
      } else {
        updateEntityLabel(container, entity.name);
      }
      container.position.set(
        entity.position.x * TILE_SIZE + TILE_SIZE / 2,
        entity.position.y * TILE_SIZE + TILE_SIZE / 2,
      );
    }
    for (const [id, container] of [...this.entitySprites]) {
      if (!seen.has(id)) {
        this.entityLayer.removeChild(container);
        container.destroy({ children: true });
    this.entitySprites.delete(id);
      }
    }
  }
}
