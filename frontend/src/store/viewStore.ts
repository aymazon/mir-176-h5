// Non-React view state: applies server snapshots with seq-based staleness
// discard, keeps a prev/curr position buffer per tracked entity, and provides
// a 300ms linear-interpolation reader for the render loop (W1T7).

import type { CharReadyMsg, CharacterState, SnapshotMsg } from '../net/protocol';

export interface Vec2 {
  readonly x: number;
  readonly y: number;
}

/** Interpolation window: the view trails the newest snapshot by this much. */
export const INTERPOLATION_WINDOW_MS = 300;

export function clampToRange(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function lerpVec2(from: Vec2, to: Vec2, alpha: number): Vec2 {
  return {
    x: from.x + (to.x - from.x) * alpha,
    y: from.y + (to.y - from.y) * alpha,
  };
}

/**
 * Mutable double buffer: the documented purpose of this object is to be
 * rewritten by every applied snapshot (prev <- curr, curr <- new position).
 */
interface PositionTrack {
  name: string;
  prev: { x: number; y: number };
  curr: { x: number; y: number };
  windowStartMs: number;
}

export interface VisibleEntity {
  readonly id: number;
  readonly name: string;
  readonly position: Vec2;
}

export class ViewStore {
  private characterState: CharacterState | undefined;
  private characterTrack: PositionTrack | undefined;
  private readonly entities = new Map<number, PositionTrack>();
  private appliedSeq: number | null = null;

  get character(): CharacterState | undefined {
    return this.characterState;
  }

  get lastAppliedSeq(): number | null {
    return this.appliedSeq;
  }

  get entityCount(): number {
    return this.entities.size;
  }

  /** char_ready: full character state plus the initial vision snapshot. */
  applyCharReady(message: CharReadyMsg, nowMs: number): boolean {
    this.characterState = message.character;
    return this.applySnapshot(message.vision, nowMs);
  }

  /** Returns false when the snapshot was discarded as stale (seq <= last applied). */
  applySnapshot(snapshot: SnapshotMsg, nowMs: number): boolean {
    if (this.appliedSeq !== null && snapshot.seq <= this.appliedSeq) return false;
    this.appliedSeq = snapshot.seq;
    this.characterState = snapshot.char;
    const characterTrack = this.characterTrack;
    if (characterTrack === undefined) {
      this.characterTrack = this.newTrack(snapshot.char.x, snapshot.char.y, snapshot.char.name, nowMs);
    } else {
      this.updateTrack(characterTrack, snapshot.char.x, snapshot.char.y, snapshot.char.name, nowMs);
    }

    const visible = new Set<number>();
    for (const entity of snapshot.entities) {
      visible.add(entity.id);
      const existing = this.entities.get(entity.id);
      if (existing === undefined) {
        this.entities.set(entity.id, this.newTrack(entity.x, entity.y, entity.name, nowMs));
      } else {
        this.updateTrack(existing, entity.x, entity.y, entity.name, nowMs);
      }
    }
    for (const id of [...this.entities.keys()]) {
      if (!visible.has(id)) this.entities.delete(id);
    }
    return true;
  }

  /** Interpolated position of a tracked entity, or of the local char by its id. */
  positionAt(id: number, nowMs: number): Vec2 | undefined {
    const characterState = this.characterState;
    const characterTrack = this.characterTrack;
    if (characterState !== undefined && characterTrack !== undefined && id === characterState.id) {
      return this.positionOf(characterTrack, nowMs);
    }
    const track = this.entities.get(id);
    return track === undefined ? undefined : this.positionOf(track, nowMs);
  }

  characterPositionAt(nowMs: number): Vec2 | undefined {
    return this.characterTrack === undefined ? undefined : this.positionOf(this.characterTrack, nowMs);
  }

  visibleEntities(nowMs: number): readonly VisibleEntity[] {
    const result: VisibleEntity[] = [];
    for (const [id, track] of this.entities) {
      result.push({ id, name: track.name, position: this.positionOf(track, nowMs) });
    }
    return result;
  }

  /** Drop all state; call after a reconnect (seq is monotonic per connection). */
  reset(): void {
    this.characterState = undefined;
    this.characterTrack = undefined;
    this.entities.clear();
    this.appliedSeq = null;
  }

  private newTrack(x: number, y: number, name: string, nowMs: number): PositionTrack {
    return { name, prev: { x, y }, curr: { x, y }, windowStartMs: nowMs };
  }

  private updateTrack(track: PositionTrack, x: number, y: number, name: string, nowMs: number): void {
    track.prev = track.curr;
    track.curr = { x, y };
    track.name = name;
    track.windowStartMs = nowMs;
  }

  private positionOf(track: PositionTrack, nowMs: number): Vec2 {
    const elapsed = clampToRange(nowMs - track.windowStartMs, 0, INTERPOLATION_WINDOW_MS);
    return lerpVec2(track.prev, track.curr, elapsed / INTERPOLATION_WINDOW_MS);
  }
}
