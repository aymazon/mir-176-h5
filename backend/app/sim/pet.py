"""召唤物子系统（todo 16）：诱惑之光 / 召唤骷髅 / 召唤神兽。

职责边界（计划红线）：
- Pet 是 ZoneInstance 级**内存实体**，随主人 PlayerSession 生命周期；
  **不入 DB**、不做 alembic 迁移。session 注销（release_session / 断线宽限
  超时 / 显式登出）由生命周期持有方调 :func:`disperse_all` 清空——
  ``tick_loop.py`` / ``session.py`` 本任务冻结（仅 session.py 新增
  ``pets`` 字段），接线点在使用方（e2e 真机召狗验收见 todo 28）。
- **依赖倒置**：本模块不 import ``app.sim.session``（它 TYPE_CHECKING 引用
  本模块的 ``Pet``）；主人会话以 :class:`OwnerSession` 协议（最小依赖面）
  注解，运行期经模块级辅助注册表 ``_owner_sessions``（summon 时登记、
  disperse_all 时注销）反查。
- 技能定义由调用方注入 ``SkillRegistry``（测试用 tests/fixtures/skills_32.json），
  缺省回落真实 gamedata（``load_registry()``）；本模块不直接读 W3 全量数据。
- pet AI 最小集（D12 距离档模型，同 tick_loop）：跟随主人目标
  （``zone.alive_monsters[0]``），只打怪物——**不打玩家、不打他宠**。
- 战斗复用 combat 纯函数（``hit_check`` / ``physical_damage``）；数值系数
  一律来自 ``app.combat.balance``——本文件禁止 0.x 小数字面量（同
  tests/test_balance.py 的 grep 纪律）。

SSE 事件：``pet_summon`` / ``pet_died`` / ``pet_dispersed``（publish 在锁外，
与 tick_loop 同一纪律）；pet 击杀经验复用 ``exp`` 事件（data 带 ``by="pet"``）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from random import Random
from typing import TYPE_CHECKING, Final, Protocol
from uuid import uuid4

from app.combat import balance
from app.combat.damage import physical_damage
from app.combat.hit import hit_check
from app.combat.skills import SkillRegistry, SkillState, load_registry
from app.gamedata_schema.monsters import MonsterDef
from app.sse.session_state import SessionState

if TYPE_CHECKING:
    from app.sim.zone import MonsterInstance, ZoneInstance

LOG = logging.getLogger(__name__)

__all__ = [
    "OwnerSession",
    "Pet",
    "PetType",
    "attack_target",
    "disperse",
    "disperse_all",
    "owner_of",
    "pets_of",
    "summon_pet",
    "tick_pets",
]

# SSE 事件名（与 tick_loop 的 EV_LOG/EV_STAT 同一推送通道）
EV_PET_SUMMON: Final[str] = "pet_summon"
EV_PET_DIED: Final[str] = "pet_died"
EV_PET_DISPERSED: Final[str] = "pet_dispersed"
EV_EXP: Final[str] = "exp"
EV_LOG: Final[str] = "log"


class PetType(StrEnum):
    """召唤物种类：charm=诱惑之光魅惑的怪 / skull=召唤骷髅 / dog=召唤神兽。"""

    charm = "charm"
    skull = "skull"
    dog = "dog"


# 技能 id → 召唤物种类（summon_dog 为 summon_beast 的别名，防计划文本两种叫法漂移）
_SKILL_TO_PET_TYPE: Final[dict[str, PetType]] = {
    "temptation": PetType.charm,
    "summon_skeleton": PetType.skull,
    "summon_beast": PetType.dog,
    "summon_dog": PetType.dog,
}

# 合成召唤物 MonsterDef 的占位基准等级（skill_level 0 级时的等级；int 字面量非系数）
_SUMMON_BASE_LEVEL: Final[int] = 1


@dataclass(slots=True)
class Pet:
    """一只召唤物的内存态（可变：hp/target 随 tick 推进；不持久化）。"""

    pet_id: str
    owner_character_id: int
    pet_type: PetType
    monster_def: MonsterDef
    skill_id: str
    skill_level: int
    hp: int
    max_hp: int
    attack_min: int
    attack_max: int
    # 跟随主人目标：zone.alive_monsters 里的 instance_id；None = idle
    target_monster_id: str | None = None
    # 本宠累计为主人赚到的经验（pet 击杀归主人的累计账）
    exp_yield_to_owner: int = 0


class _SnapshotLike(Protocol):
    """pet 结算只读/累加的角色快照面（避免反向引用 session.CharacterSnapshot）。"""

    level: int
    exp: int


class OwnerSession(Protocol):
    """pet 模块对主人会话的最小依赖面（结构型；PlayerSession 天然满足）。

    全部声明为只读 property（协变）：pet 只读引用或就地改内容，从不重新
    绑定属性本身——PlayerSession 的 dataclass 可变字段天然满足只读协议。
    本协议使 pet.py 无需 import app.sim.session——后者 TYPE_CHECKING 引用
    ``Pet``，单向依赖防静态 import 环（basedpyright reportImportCycles）。
    """

    @property
    def character_id(self) -> int: ...

    @property
    def char_snapshot(self) -> _SnapshotLike: ...

    @property
    def skill_states(self) -> dict[str, SkillState]: ...

    @property
    def monster_hp(self) -> dict[str, int]: ...

    @property
    def pets(self) -> list[Pet]: ...

    @property
    def rng(self) -> Random: ...

    @property
    def sse_state(self) -> SessionState: ...

    @property
    def zone(self) -> ZoneInstance | None: ...


# 主人会话辅助注册表（任务规格允许）：character_id → 会话。
# summon_pet 登记（同 id 覆盖，重连新会话自动换绑）；disperse_all 注销。
_owner_sessions: dict[int, OwnerSession] = {}


def summon_pet(
    session: OwnerSession,
    skill_id: str,
    target: MonsterInstance | MonsterDef | str | None = None,
    *,
    registry: SkillRegistry | None = None,
    monsters: dict[str, MonsterDef] | None = None,
) -> Pet:
    """按技能召唤/魅惑一只 pet，登记进 ``session.pets`` 并发 SSE pet_summon。

    - ``temptation``（诱惑之光）：``target`` 必须指向怪物（MonsterInstance /
      MonsterDef / monster_id str + ``monsters`` 注册表）；数量上限
      ``CHARM_MAX_PER_CASTER``、等级宽容 ``CHARM_LEVEL_TOLERANCE`` 两道校验。
    - ``summon_skeleton`` / ``summon_beast``：固定模板合成 MonsterDef，
      HP 按 ``SKULL_/DOG_BASE_HP + GROWTH_PER_LEVEL × skill_level`` 缩放；
      攻击力取技能 ``effect_params`` 的 ``pet_dc_min/pet_dc_max``。
    - 未知技能 id → SkillNotFoundError（registry.get 的 fail-fast 语义）；
      非召唤类技能 / 超上限 / 目标非法 → ValueError（message 带人话原因）。
    """
    reg = registry if registry is not None else load_registry()
    skill = reg.get(skill_id)
    pet_type = _SKILL_TO_PET_TYPE.get(skill.id)
    if pet_type is None:
        raise ValueError(f"技能 {skill.id}（{skill.name}）不是召唤/诱惑类技能")
    state = session.skill_states.get(skill.id)
    skill_level = state.level if state is not None else 0

    if pet_type is PetType.charm:
        monster_def = _resolve_charm_target(session, target, monsters)
        attack_min = monster_def.attack
        attack_max = monster_def.attack
        max_hp = monster_def.hp
    else:
        attack_min = int(skill.effect_params.get("pet_dc_min", balance.MIN_DAMAGE))
        attack_max = int(skill.effect_params.get("pet_dc_max", balance.MIN_DAMAGE))
        if pet_type is PetType.skull:
            max_hp = (
                balance.SKULL_BASE_HP + balance.SKULL_GROWTH_PER_LEVEL * skill_level
            )
        else:
            max_hp = balance.DOG_BASE_HP + balance.DOG_GROWTH_PER_LEVEL * skill_level
        monster_def = _summon_def(pet_type, skill_level, max_hp, attack_max)

    pet = Pet(
        pet_id=uuid4().hex,
        owner_character_id=session.character_id,
        pet_type=pet_type,
        monster_def=monster_def,
        skill_id=skill.id,
        skill_level=skill_level,
        hp=max_hp,
        max_hp=max_hp,
        attack_min=attack_min,
        attack_max=attack_max,
    )
    session.pets.append(pet)
    _owner_sessions[session.character_id] = session
    session.sse_state.publish(
        EV_PET_SUMMON,
        json.dumps(
            {
                "pet_id": pet.pet_id,
                "pet_type": pet.pet_type.value,
                "name": monster_def.name,
                "hp": pet.hp,
                "max_hp": pet.max_hp,
                "skill_id": skill.id,
                "skill_level": skill_level,
            },
            ensure_ascii=False,
        ),
    )
    LOG.info(
        "pet summoned: %s (%s) for character %s",
        pet.pet_id,
        pet.pet_type,
        session.character_id,
    )
    return pet


def tick_pets(session: OwnerSession, dt_ticks: int = 1) -> list[Pet]:
    """推进 session 全部 pet 一 tick，返回仍存活的 pet 列表。

    - hp ≤ 0 → 移除 + SSE pet_died（经验不归任何人——非主人所杀）；
    - 目标怪已不在场（被主人击杀）→ 切换到主人当前目标
      （``zone.alive_monsters[0]``，D12 距离档）或 idle；
    - 有目标则出手一次（:func:`attack_target`）。

    ``dt_ticks`` 为距上次的 tick 数（MVP 每拍出手一次，参数仅为未来
    攻速节流预留；必须 ≥ 0）。纯内存结算，无锁无 DB。
    """
    if dt_ticks < 0:
        raise ValueError(f"dt_ticks 必须 ≥ 0，实得 {dt_ticks}")
    zone = session.zone
    alive = {m.instance_id: m for m in zone.alive_monsters} if zone is not None else {}
    for pet in list(session.pets):
        if pet.hp <= 0:
            session.pets.remove(pet)
            session.sse_state.publish(
                EV_PET_DIED,
                json.dumps(
                    {"pet_id": pet.pet_id, "pet_type": pet.pet_type.value},
                    ensure_ascii=False,
                ),
            )
            continue
        target = alive.get(pet.target_monster_id) if pet.target_monster_id else None
        if target is None:
            target = (
                zone.alive_monsters[0]
                if zone is not None and zone.alive_monsters
                else None
            )
            pet.target_monster_id = target.instance_id if target is not None else None
        if target is not None:
            attack_target(pet, target)
    return list(session.pets)


def attack_target(pet: Pet, target: MonsterInstance) -> int:
    """pet 对目标怪物出手一次：命中 roll → 伤害结算 → 击杀经验归主人。

    返回造成的伤害（未命中返回 0）。击杀时把 ``target.monster_def.exp``
    累加进主人 ``char_snapshot.exp`` 与本宠 ``exp_yield_to_owner``，
    并复用 ``exp`` SSE 事件（``by="pet"``）。主人会话已注销（辅助注册表
    无记录或 pet 已不在其 pets 列表）则静默 0。
    """
    session = _owner_sessions.get(pet.owner_character_id)
    if session is None or pet not in session.pets:
        return 0
    rng = session.rng
    if not hit_check(rng, pet.monster_def.accuracy, target.monster_def.agility):
        session.sse_state.publish(
            EV_LOG,
            json.dumps(
                {
                    "kind": "pet_miss",
                    "text": f"{pet.monster_def.name}攻击{target.monster_def.name}未命中",
                    "pet_id": pet.pet_id,
                    "target_id": target.instance_id,
                },
                ensure_ascii=False,
            ),
        )
        return 0
    dmg = physical_damage(
        rng,
        attack_min=pet.attack_min,
        attack_max=pet.attack_max,
        target_defense=target.monster_def.defense,
    )
    remaining = (
        session.monster_hp.setdefault(target.instance_id, target.monster_def.hp) - dmg
    )
    if remaining > 0:
        session.monster_hp[target.instance_id] = remaining
        return dmg
    # 击杀：经验归主人（pet 击杀归属规则）
    zone = session.zone
    killed = zone.kill_monster(target.instance_id) if zone is not None else target
    session.monster_hp.pop(target.instance_id, None)
    if pet.target_monster_id == target.instance_id:
        pet.target_monster_id = None
    slain = killed if killed is not None else target
    exp_gain = slain.monster_def.exp
    session.char_snapshot.exp += exp_gain
    pet.exp_yield_to_owner += exp_gain
    session.sse_state.publish(
        EV_EXP,
        json.dumps(
            {
                "amount": exp_gain,
                "total": session.char_snapshot.exp,
                "monster_id": slain.monster_id,
                "by": "pet",
                "pet_id": pet.pet_id,
            },
            ensure_ascii=False,
        ),
    )
    session.sse_state.publish(
        EV_LOG,
        json.dumps(
            {
                "kind": "pet_kill",
                "text": f"{pet.monster_def.name}击杀了{slain.monster_def.name}，你获得 {exp_gain} 经验",
                "pet_id": pet.pet_id,
                "target_id": slain.instance_id,
                "monster_id": slain.monster_id,
                "exp": exp_gain,
            },
            ensure_ascii=False,
        ),
    )
    return dmg


def disperse(pet: Pet) -> bool:
    """移除一只 pet 并发 SSE pet_dispersed；主人会话已注销/不在册返回 False。"""
    session = _owner_sessions.get(pet.owner_character_id)
    if session is None or pet not in session.pets:
        return False
    session.pets.remove(pet)
    session.sse_state.publish(
        EV_PET_DISPERSED,
        json.dumps(
            {"pet_id": pet.pet_id, "pet_type": pet.pet_type.value},
            ensure_ascii=False,
        ),
    )
    return True


def disperse_all(session: OwnerSession) -> int:
    """清空 session 全部 pet 并注销辅助注册表（session 注销/宽限超时的生命周期钩子）。

    直接作用于 session 对象——即使 session 已被 ``release_session`` 弹出
    全局注册表也可安全调用。返回解散数量。不持久化。
    """
    n = 0
    for pet in list(session.pets):
        session.pets.remove(pet)
        session.sse_state.publish(
            EV_PET_DISPERSED,
            json.dumps(
                {"pet_id": pet.pet_id, "pet_type": pet.pet_type.value},
                ensure_ascii=False,
            ),
        )
        n += 1
    _owner_sessions.pop(session.character_id, None)
    return n


def owner_of(pet: Pet) -> int:
    """pet → 主人 character_id（pet 自携带，反查零成本）。"""
    return pet.owner_character_id


def pets_of(session: OwnerSession) -> list[Pet]:
    """当前角色全部存活 pet 的只读快照（列表拷贝，防遍历期变更）。"""
    return list(session.pets)


# --- 内部 helpers ---------------------------------------------------------------


def _resolve_charm_target(
    session: OwnerSession,
    target: MonsterInstance | MonsterDef | str | None,
    monsters: dict[str, MonsterDef] | None,
) -> MonsterDef:
    """诱惑之光目标解析 + 数量/等级两道闸；返回被魅惑怪的 MonsterDef。"""
    charm_count = sum(1 for p in session.pets if p.pet_type is PetType.charm)
    if charm_count >= balance.CHARM_MAX_PER_CASTER:
        raise ValueError(
            f"诱惑之光已达数量上限 {balance.CHARM_MAX_PER_CASTER} 只，无法再魅惑"
        )
    monster_def: MonsterDef | None = None
    if isinstance(target, MonsterDef):
        monster_def = target
    elif isinstance(target, str):
        if monsters is not None:
            monster_def = monsters.get(target)
        if monster_def is None:
            raise ValueError(f"诱惑目标怪物 {target!r} 不在 monsters 注册表中")
    elif target is not None:  # MonsterInstance（鸭子类型，避免 runtime import zone）
        monster_def = target.monster_def
    if monster_def is None:
        raise ValueError(
            "诱惑之光需要指定目标怪物（MonsterInstance/MonsterDef/monster_id）"
        )
    tolerance = balance.CHARM_LEVEL_TOLERANCE
    if monster_def.level > session.char_snapshot.level + tolerance:
        raise ValueError(
            f"怪物 {monster_def.name}（Lv{monster_def.level}）超过诱惑上限"
            f"（主人 Lv{session.char_snapshot.level} + {tolerance}）"
        )
    return monster_def


def _summon_def(
    pet_type: PetType, skill_level: int, max_hp: int, attack_max: int
) -> MonsterDef:
    """召唤骷髅/神兽的合成 MonsterDef（固定模板，exp=0：杀宠无经验）。"""
    is_skull = pet_type is PetType.skull
    return MonsterDef(
        id=f"pet-{pet_type.value}",
        name="骷髅" if is_skull else "神兽",
        level=_SUMMON_BASE_LEVEL + skill_level,
        hp=max_hp,
        attack=attack_max,
        defense=0,
        magic_defense=0,
        accuracy=attack_max,
        agility=0,
        exp=0,
        attack_speed=1,
        move_speed=0,
        undead=is_skull,
        boss=False,
        respawn_seconds=None,
    )
