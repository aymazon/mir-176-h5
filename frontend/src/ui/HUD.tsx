// Top HUD: level / map / gold, HP+MP bars (level-1 base max, W1 has no
// server max fields), numeric exp readout. Re-renders on a 200ms throttle.

import { useEffect, useReducer } from 'react';

import type { ViewStore } from '../store/viewStore';

const HUD_REFRESH_MS = 200;

interface BaseMax {
  readonly hp: number;
  readonly mp: number;
}

// Mirrors the backend level-1 base stats (_LEVEL1_BASE_STATS).
const LEVEL1_BASE_MAX: Record<string, BaseMax> = {
  warrior: { hp: 33, mp: 11 },
  mage: { hp: 11, mp: 33 },
  taoist: { hp: 22, mp: 22 },
};

function baseMaxFor(classId: string): BaseMax {
  return LEVEL1_BASE_MAX[classId] ?? LEVEL1_BASE_MAX.warrior;
}

function clampPercent(value: number, max: number): number {
  if (max <= 0) return 0;
  return Math.min(100, Math.max(0, (value / max) * 100));
}

interface HUDProps {
  readonly viewStore: ViewStore;
}

export default function HUD({ viewStore }: HUDProps) {
  const [, forceRender] = useReducer((count: number) => count + 1, 0);

  useEffect(() => {
    const timer = window.setInterval(forceRender, HUD_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);

  const character = viewStore.character;
  if (character === undefined) return null;

  const base = baseMaxFor(character.class);
  const hpPercent = clampPercent(character.hp, base.hp);
  const mpPercent = clampPercent(character.mp, base.mp);

  return (
    <header className="hud">
      <div className="hud__stats">
        <span>Lv.{character.level}</span>
        <span>地图 {character.map_id}</span>
        <span className="hud__gold">金币 {character.gold}</span>
      </div>
      <div
        className="bar bar--hp"
        role="progressbar"
        aria-label="HP"
        aria-valuenow={character.hp}
        aria-valuemin={0}
        aria-valuemax={base.hp}
      >
        <span className="bar__fill" style={{ width: `${hpPercent}%` }} />
        <span className="bar__label">
          HP {character.hp}/{base.hp}
        </span>
      </div>
      <div
        className="bar bar--mp"
        role="progressbar"
        aria-label="MP"
        aria-valuenow={character.mp}
        aria-valuemin={0}
        aria-valuemax={base.mp}
      >
        <span className="bar__fill" style={{ width: `${mpPercent}%` }} />
        <span className="bar__label">
          MP {character.mp}/{base.mp}
        </span>
      </div>
      <div className="hud__exp">经验: {character.exp}</div>
    </header>
  );
}
