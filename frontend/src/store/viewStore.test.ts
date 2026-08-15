import { describe, expect, it } from 'vitest';

import type { CharReadyMsg, CharacterState, SnapshotMsg } from '../net/protocol';
import { RECONNECT_BASE_MS, RECONNECT_MAX_MS, reconnectDelayMs } from '../net/wsClient';
import { INTERPOLATION_WINDOW_MS, ViewStore, clampToRange, lerpVec2 } from './viewStore';

const CHAR: CharacterState = {
  id: 1,
  name: 'Hero',
  class: 'warrior',
  level: 30,
  exp: 100,
  hp: 200,
  mp: 50,
  gold: 5,
  bound_city: 'bicheng',
  map_id: 'bicheng',
  x: 0,
  y: 0,
};

interface EntityFixture {
  id: number;
  name: string;
  x: number;
  y: number;
}

function makeSnapshot(
  seq: number,
  overrides: { char?: Partial<CharacterState>; entities?: EntityFixture[] } = {},
): SnapshotMsg {
  return {
    t: 'snapshot',
    seq,
    char: { ...CHAR, ...(overrides.char ?? {}) },
    entities: (overrides.entities ?? []).map((entity) => ({ kind: 'player' as const, ...entity })),
  };
}

describe('reconnectDelayMs', () => {
  it('doubles from 1s and stays capped at 10s', () => {
    const delays = Array.from({ length: 8 }, (_, attempt) => reconnectDelayMs(attempt));
    expect(delays).toEqual([1000, 2000, 4000, 8000, 10000, 10000, 10000, 10000]);
  });

  it('is anchored on the exported constants', () => {
    expect(RECONNECT_BASE_MS).toBe(1000);
    expect(RECONNECT_MAX_MS).toBe(10000);
    expect(reconnectDelayMs(0)).toBe(RECONNECT_BASE_MS);
    expect(reconnectDelayMs(10)).toBe(RECONNECT_MAX_MS);
  });

  it('rejects invalid attempt counts', () => {
    expect(() => reconnectDelayMs(-1)).toThrow(RangeError);
    expect(() => reconnectDelayMs(1.5)).toThrow(RangeError);
  });
});

describe('ViewStore seq handling', () => {
  it('applies the first snapshot and tracks its seq', () => {
    const store = new ViewStore();
    expect(store.applySnapshot(makeSnapshot(5, { char: { x: 5, y: 5 } }), 1000)).toBe(true);
    expect(store.lastAppliedSeq).toBe(5);
    expect(store.character?.x).toBe(5);
  });

  it('discards an out-of-order snapshot with seq <= last applied', () => {
    const store = new ViewStore();
    store.applySnapshot(
      makeSnapshot(5, {
        char: { x: 5, y: 5 },
        entities: [{ id: 2, name: 'Other', x: 20, y: 20 }],
      }),
      1000,
    );

    const stale = makeSnapshot(3, {
      char: { x: 99, y: 99 },
      entities: [{ id: 2, name: 'Other', x: 1, y: 1 }],
    });
    expect(store.applySnapshot(stale, 1300)).toBe(false);
    expect(store.lastAppliedSeq).toBe(5);
    expect(store.character?.x).toBe(5);
    expect(store.character?.y).toBe(5);
    expect(store.positionAt(2, 1300 + INTERPOLATION_WINDOW_MS)).toEqual({ x: 20, y: 20 });
  });

  it('applies a newer snapshot after a stale one was discarded', () => {
    const store = new ViewStore();
    store.applySnapshot(makeSnapshot(5), 1000);
    store.applySnapshot(makeSnapshot(3, { char: { x: 99, y: 99 } }), 1100);
    expect(store.applySnapshot(makeSnapshot(6, { char: { x: 6, y: 6 } }), 1200)).toBe(true);
    expect(store.lastAppliedSeq).toBe(6);
    expect(store.character?.x).toBe(6);
  });

  it('discards a duplicate of the last applied seq', () => {
    const store = new ViewStore();
    store.applySnapshot(makeSnapshot(5), 1000);
    expect(store.applySnapshot(makeSnapshot(5, { char: { x: 42, y: 42 } }), 1100)).toBe(false);
    expect(store.character?.x).toBe(0);
  });

  it('reset clears state so a fresh per-connection seq space applies', () => {
    const store = new ViewStore();
    store.applySnapshot(makeSnapshot(5), 1000);
    store.reset();
    expect(store.lastAppliedSeq).toBeNull();
    expect(store.character).toBeUndefined();
    expect(store.entityCount).toBe(0);
    expect(store.applySnapshot(makeSnapshot(1), 2000)).toBe(true);
    expect(store.lastAppliedSeq).toBe(1);
  });
});

describe('ViewStore interpolation', () => {
  it('lerpVec2 blends linearly between the two endpoints', () => {
    expect(lerpVec2({ x: 0, y: 0 }, { x: 10, y: 0 }, 0)).toEqual({ x: 0, y: 0 });
    expect(lerpVec2({ x: 0, y: 0 }, { x: 10, y: 0 }, 0.5)).toEqual({ x: 5, y: 0 });
    expect(lerpVec2({ x: 0, y: 0 }, { x: 10, y: 0 }, 1)).toEqual({ x: 10, y: 0 });
    expect(lerpVec2({ x: 0, y: 2 }, { x: 0, y: 8 }, 0.25)).toEqual({ x: 0, y: 3.5 });
  });

  it('clampToRange clamps into [min, max]', () => {
    expect(clampToRange(-5, 0, 300)).toBe(0);
    expect(clampToRange(150, 0, 300)).toBe(150);
    expect(clampToRange(999, 0, 300)).toBe(300);
  });

  it('returns prev at t=0, the midpoint at t=150, and curr at t>=300', () => {
    const store = new ViewStore();
    store.applySnapshot(makeSnapshot(1, { entities: [{ id: 7, name: 'Other', x: 0, y: 0 }] }), 0);
    store.applySnapshot(makeSnapshot(2, { entities: [{ id: 7, name: 'Other', x: 10, y: 0 }] }), 0);

    expect(store.positionAt(7, 0)).toEqual({ x: 0, y: 0 });
    expect(store.positionAt(7, 150)).toEqual({ x: 5, y: 0 });
    expect(store.positionAt(7, 300)).toEqual({ x: 10, y: 0 });
    expect(store.positionAt(7, 10_000)).toEqual({ x: 10, y: 0 });
  });

  it('does not move a brand-new entity (prev == curr on first sight)', () => {
    const store = new ViewStore();
    store.applySnapshot(makeSnapshot(1, { entities: [{ id: 7, name: 'Other', x: 4, y: 6 }] }), 0);
    expect(store.positionAt(7, 0)).toEqual({ x: 4, y: 6 });
    expect(store.positionAt(7, 150)).toEqual({ x: 4, y: 6 });
    expect(store.positionAt(7, 300)).toEqual({ x: 4, y: 6 });
  });

  it('interpolates the local character and resolves it by its id', () => {
    const store = new ViewStore();
    store.applySnapshot(makeSnapshot(1, { char: { x: 0, y: 0 } }), 0);
    store.applySnapshot(makeSnapshot(2, { char: { x: 10, y: 0 } }), 0);
    expect(store.positionAt(CHAR.id, 150)).toEqual({ x: 5, y: 0 });
    expect(store.characterPositionAt(150)).toEqual({ x: 5, y: 0 });
  });

  it('drops entities that leave the vision', () => {
    const store = new ViewStore();
    store.applySnapshot(makeSnapshot(1, { entities: [{ id: 7, name: 'Other', x: 0, y: 0 }] }), 0);
    store.applySnapshot(makeSnapshot(2, { entities: [] }), 200);
    expect(store.entityCount).toBe(0);
    expect(store.positionAt(7, 200)).toBeUndefined();
  });

  it('lists visible entities with their interpolated positions', () => {
    const store = new ViewStore();
    store.applySnapshot(makeSnapshot(1, { entities: [{ id: 7, name: 'Other', x: 0, y: 0 }] }), 0);
    store.applySnapshot(makeSnapshot(2, { entities: [{ id: 7, name: 'Other', x: 10, y: 0 }] }), 0);
    expect(store.visibleEntities(150)).toEqual([{ id: 7, name: 'Other', position: { x: 5, y: 0 } }]);
  });
});

describe('ViewStore char_ready', () => {
  it('applies the initial character plus the vision snapshot', () => {
    const store = new ViewStore();
    const message: CharReadyMsg = {
      t: 'char_ready',
      character: { ...CHAR, x: 10, y: 10 },
      vision: makeSnapshot(1, { char: { x: 10, y: 10 }, entities: [{ id: 2, name: 'Other', x: 11, y: 11 }] }),
      inventory: { items: [{ code: 'potion', slot: 1, quantity: 3 }], gold: 5 },
      skills: { skills: [{ code: 'cleave', rank: 5, exp: 0 }] },
      quest_state: { quests: [{ id: 'q1', status: 'active', progress: 1 }] },
    };

    expect(store.applyCharReady(message, 0)).toBe(true);
    expect(store.character?.id).toBe(CHAR.id);
    expect(store.lastAppliedSeq).toBe(1);
    expect(store.entityCount).toBe(1);
    expect(store.positionAt(2, 0)).toEqual({ x: 11, y: 11 });
  });
});
