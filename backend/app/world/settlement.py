"""Combat settlement pipeline: one defender's fight ledger (MVP-DESIGN 4.3/6.4).

The pure math lives in :mod:`app.world.combat`; this module owns the stateful
part: per-attacker damage contributions, hp, and the exactly-once death
resolution that turns a kill into exp (F9) and gold (F10) for the credited
killer. The auto-battle state machine (task 20) instantiates one
:class:`Settlement` per monster fight; drops (task 18) consume
:class:`KillRewards`.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.content.models import MonsterDef
from app.world.combat import (
    AttackKind,
    AttackerKind,
    Mitigation,
    exp_award,
    kill_credit,
    roll_attack_damage,
    roll_gold,
    roll_hit,
    skill_multiplier,
)


@dataclass(frozen=True, slots=True)
class Attack:
    """One declared attack: which formula it uses and its skill scaling (F7)."""

    kind: AttackKind
    multiplier: float = 1.0
    skill_rank: int = 0


@dataclass(frozen=True, slots=True)
class Attacker:
    """One side of a fight: identity, credit kind, and its combat stats (doc 4.2/4.3)."""

    id: str
    kind: AttackerKind
    accuracy: int = 0
    dodge: int = 0
    attack_min: int = 0
    attack_max: int = 0
    magic_min: int = 0
    magic_max: int = 0
    taoist_min: int = 0
    taoist_max: int = 0
    owner_id: str | None = None  # summon only: F9 routes exp and drops to this id


@dataclass(frozen=True, slots=True)
class KillRewards:
    """F9/F10 resolution for one kill: who is credited and what they receive.

    ``summon_owner_id`` is set when the credited killer is a summon: exp is
    halved (F9) and the gold goes to the owner's bag, not the summon's.
    """

    credited_id: str
    exp: int
    gold: int
    summon_owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class AttackResult:
    """Outcome of one resolved attack: hit -> damage -> death -> rewards."""

    hit: bool
    damage: int
    defender_hp: int
    killed: bool
    rewards: KillRewards | None = None


@dataclass
class Settlement:
    """One defender's fight ledger: hp, per-attacker damage, one-time death resolution.

    Set ``monster`` for monster defenders so death resolves F9/F10 exactly
    once; for other defenders death only stops the fight (DEAD is task 20's).
    """

    defender_id: str
    hp: int
    monster: MonsterDef | None = None
    dodge: int = 0
    defense: Mitigation = field(default_factory=lambda: Mitigation.flat(0))
    magic_def: Mitigation = field(default_factory=lambda: Mitigation.flat(0))
    _contributions: dict[str, int] = field(default_factory=dict, repr=False)
    _attackers: dict[str, Attacker] = field(default_factory=dict, repr=False)
    _rewards: KillRewards | None = field(default=None, init=False, repr=False)

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def contributions(self) -> Mapping[str, int]:
        """Per-attacker damage dealt to the defender (6.4 kill-credit input)."""
        return self._contributions

    @property
    def rewards(self) -> KillRewards | None:
        """The kill rewards, resolved exactly once; None until (and unless) resolved."""
        return self._rewards

    def attack(self, rng: random.Random, attacker: Attacker, attack: Attack) -> AttackResult:
        """Resolve one attack end to end: F1 hit -> F2/F4/F6 damage -> hp -> death rewards."""
        if not self.alive:
            return AttackResult(hit=False, damage=0, defender_hp=self.hp, killed=False)
        if not roll_hit(rng, attacker.accuracy, self.dodge):
            return AttackResult(hit=False, damage=0, defender_hp=self.hp, killed=False)
        stat_min, stat_max = _stat_range(attacker, attack.kind)
        multiplier = skill_multiplier(attack.multiplier, attack.skill_rank)
        damage = roll_attack_damage(rng, stat_min, stat_max, multiplier, _mitigation(self, attack.kind))
        return self.apply_damage(rng, attacker, damage)

    def apply_damage(self, rng: random.Random, attacker: Attacker, damage: int) -> AttackResult:
        """Record damage from any source (attack roll, DoT tick); resolves kill rewards once."""
        if not self.alive:
            return AttackResult(hit=False, damage=0, defender_hp=self.hp, killed=False)
        self.hp = max(0, self.hp - damage)
        self._contributions[attacker.id] = self._contributions.get(attacker.id, 0) + damage
        if attacker.id not in self._attackers:
            self._attackers[attacker.id] = attacker
        if self.alive:
            return AttackResult(hit=True, damage=damage, defender_hp=self.hp, killed=False)
        return AttackResult(hit=True, damage=damage, defender_hp=0, killed=True, rewards=self.settle_kill(rng))

    def settle_kill(self, rng: random.Random) -> KillRewards | None:
        """Resolve F9/F10 for a dead monster defender; idempotent, never re-rolls."""
        if self._rewards is not None:
            return self._rewards
        if self.alive or self.monster is None:
            return None
        credited_id = kill_credit(self._contributions)
        if credited_id is None:
            return None
        credited = self._attackers[credited_id]
        match credited.kind:
            case "player":
                self._rewards = KillRewards(
                    credited_id=credited_id,
                    exp=exp_award(self.monster.exp, "player"),
                    gold=roll_gold(rng, self.monster.gold_min, self.monster.gold_max),
                )
            case "summon":
                self._rewards = KillRewards(
                    credited_id=credited_id,
                    exp=exp_award(self.monster.exp, "summon"),
                    gold=roll_gold(rng, self.monster.gold_min, self.monster.gold_max),
                    summon_owner_id=credited.owner_id,
                )
            case "monster":
                return None
        return self._rewards


def _stat_range(attacker: Attacker, kind: AttackKind) -> tuple[int, int]:
    match kind:
        case "physical":
            return attacker.attack_min, attacker.attack_max
        case "magic":
            return attacker.magic_min, attacker.magic_max
        case "fire_talisman":
            return attacker.taoist_min, attacker.taoist_max


def _mitigation(settlement: Settlement, kind: AttackKind) -> Mitigation:
    match kind:
        case "physical":
            return settlement.defense
        case "magic" | "fire_talisman":
            return settlement.magic_def
