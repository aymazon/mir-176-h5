"""sim/pet.py 测试（todo 16）：召唤物子系统全内存语义。

覆盖：
- fixture 注入三技能（temptation / summon_skeleton / summon_beast），不依赖
  W3 全量 gamedata；
- 诱惑之光：成功 / 超 CHARM_MAX_PER_CASTER 失败 / 怪物超 CHARM_LEVEL_TOLERANCE
  失败 / 目标缺失失败；
- 骷髅/神兽 HP 按 skill_level 缩放（balance 常量公式）；
- pet 击杀经验归主人（char_snapshot.exp 与 pet.exp_yield_to_owner 双记账）；
- 主人断线宽限期后狗消失（disperse_all + release_session 模拟 teardown）；
- tick_pets：宠物攻击主人目标 / 目标被主人击杀后切下一个 / pet HP 归零移除；
- 数值纪律：pet.py 禁止 0.x 小数字面量（grep 防回归，同 test_balance 纪律）。

纯内存用例：不碰 DB（无 engine.dispose 需求）；zone 用手工 ZoneInstance
+ 合成 SpawnEntry，BOSS 钩子不触发。
"""

import json
import random
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

import pytest

from app.combat import balance
from app.combat.skills import (
    SkillNotFoundError,
    SkillRegistry,
    SkillState,
    load_registry,
)
from app.gamedata_schema import MonsterDef, SpawnEntry, SpawnLocation
from app.sim.pet import (
    PetType,
    attack_target,
    disperse,
    disperse_all,
    owner_of,
    pets_of,
    summon_pet,
    tick_pets,
)
from app.sim.session import (
    CharacterSnapshot,
    PlayerSession,
    max_hp_for,
    max_mp_for,
    register_session,
    release_session,
    session_for,
)
from app.sim.session import reset_sessions as reset_player_sessions
from app.sim.zone import MonsterInstance, ZoneInstance, shutdown_all
from app.sse.session_state import get_or_create_session
from app.sse.session_state import reset_sessions as reset_sse_sessions

FIXTURE_PATH: Final[Path] = (
    Path(__file__).resolve().parent.parent / "fixtures" / "skills_32.json"
)
DECIMAL_RE: Final[re.Pattern[str]] = re.compile(r"0\.[0-9]")


@pytest.fixture(autouse=True)
async def _clean_runtime() -> AsyncIterator[None]:
    reset_player_sessions()
    reset_sse_sessions()
    await shutdown_all()
    yield
    reset_player_sessions()
    reset_sse_sessions()
    await shutdown_all()


@pytest.fixture
def registry() -> SkillRegistry:
    """32 技能 fixture 注册表（含 temptation/summon_skeleton/summon_beast）。"""
    return load_registry(FIXTURE_PATH)


def make_session(
    character_id: int = 1,
    *,
    class_: str = "wizard",
    level: int = 20,
    exp: int = 0,
) -> PlayerSession:
    """纯内存会话（假地图 → 不碰 DB）；固定 seed 复现随机流。"""
    snap = CharacterSnapshot(
        name=f"t{character_id}",
        class_=class_,
        level=level,
        exp=exp,
        hp=max_hp_for(level),
        mp=max_mp_for(level),
        gold=0,
        map_id="no-such-map",
        bind_city="yin-xing-cun",
        bag_capacity=40,
    )
    session = PlayerSession(
        character_id=character_id,
        char_snapshot=snap,
        sse_state=get_or_create_session(character_id),
        rng=random.Random(42),
    )
    register_session(session)
    return session


def make_monster_def(
    monster_id: str = "shimu",
    *,
    level: int = 10,
    hp: int = 20,
    attack: int = 5,
    defense: int = 0,
    agility: int = 0,
    exp: int = 50,
) -> MonsterDef:
    return MonsterDef(
        id=monster_id,
        name=monster_id,
        level=level,
        hp=hp,
        attack=attack,
        defense=defense,
        magic_defense=0,
        accuracy=5,
        agility=agility,
        exp=exp,
        attack_speed=1,
        move_speed=0,
        undead=False,
        boss=False,
        respawn_seconds=None,
    )


def make_zone(
    *defs: MonsterDef, map_id: str = "test-map"
) -> tuple[ZoneInstance, list[MonsterInstance]]:
    """手工 zone：每个 def 一个刷怪点并立即生成一只活怪（不走 enter_map/DB）。"""
    table: dict[str, SpawnEntry] = {}
    for d in defs:
        table[f"{map_id}:{d.id}"] = SpawnEntry(
            map_id=map_id,
            monster_id=d.id,
            density=10,
            respawn_seconds=60,
            count=1,
            locations=[SpawnLocation(x=0, y=0)],
        )
    zone = ZoneInstance(
        map_id=map_id,
        static_spawn_table=table,
        monster_defs={d.id: d for d in defs},
    )
    monsters = [zone.spawn_monster(d, table[f"{map_id}:{d.id}"]) for d in defs]
    return zone, monsters


def event_names(session: PlayerSession) -> list[str]:
    """session SSE buffer 里已发布的事件名序列。"""
    return [e.event for e in session.sse_state.buffer.after(0)]


# --- summon_pet：诱惑之光 --------------------------------------------------------


def test_temptation_success(registry: SkillRegistry) -> None:
    session = make_session(level=20)
    target = make_monster_def(level=20)  # 20 ≤ 20 + CHARM_LEVEL_TOLERANCE
    pet = summon_pet(session, "temptation", target, registry=registry)
    assert pet.pet_type is PetType.charm
    assert pet.skill_id == "temptation"
    assert pet.monster_def is target
    assert pet.hp == pet.max_hp == target.hp
    assert pet.attack_min == pet.attack_max == target.attack
    assert pet.exp_yield_to_owner == 0
    assert pets_of(session) == [pet]
    assert owner_of(pet) == session.character_id
    assert "pet_summon" in event_names(session)


def test_temptation_target_by_monster_id(registry: SkillRegistry) -> None:
    """target 传 monster_id 字符串 + monsters 注册表解析。"""
    session = make_session(level=20)
    target = make_monster_def("shimu", level=15)
    pet = summon_pet(
        session, "temptation", "shimu", registry=registry, monsters={"shimu": target}
    )
    assert pet.monster_def is target


def test_temptation_count_cap(registry: SkillRegistry) -> None:
    session = make_session(level=99)
    for i in range(balance.CHARM_MAX_PER_CASTER):
        summon_pet(
            session, "temptation", make_monster_def(f"m{i}", level=1), registry=registry
        )
    assert len(pets_of(session)) == balance.CHARM_MAX_PER_CASTER
    with pytest.raises(ValueError, match="数量上限"):
        summon_pet(
            session,
            "temptation",
            make_monster_def("one-more", level=1),
            registry=registry,
        )
    assert len(pets_of(session)) == balance.CHARM_MAX_PER_CASTER


def test_temptation_level_gap_rejected(registry: SkillRegistry) -> None:
    session = make_session(level=20)
    too_strong = make_monster_def(level=20 + balance.CHARM_LEVEL_TOLERANCE + 1)
    with pytest.raises(ValueError, match="超过诱惑上限"):
        summon_pet(session, "temptation", too_strong, registry=registry)
    assert pets_of(session) == []


def test_temptation_boundary_level_accepted(registry: SkillRegistry) -> None:
    """等级恰好等于 owner + tolerance 时应成功（边界含端点）。"""
    session = make_session(level=20)
    borderline = make_monster_def(level=20 + balance.CHARM_LEVEL_TOLERANCE)
    pet = summon_pet(session, "temptation", borderline, registry=registry)
    assert pet.pet_type is PetType.charm


def test_temptation_requires_target(registry: SkillRegistry) -> None:
    session = make_session()
    with pytest.raises(ValueError, match="需要指定目标"):
        summon_pet(session, "temptation", None, registry=registry)


def test_summon_unknown_skill_raises(registry: SkillRegistry) -> None:
    session = make_session()
    with pytest.raises(SkillNotFoundError):
        summon_pet(session, "no_such_skill", None, registry=registry)


def test_non_summon_skill_rejected(registry: SkillRegistry) -> None:
    session = make_session()
    with pytest.raises(ValueError, match="不是召唤/诱惑类技能"):
        summon_pet(session, "fireball", None, registry=registry)


# --- summon_pet：骷髅 / 神兽 HP 缩放 ----------------------------------------------


def test_skull_hp_scales_with_skill_level(registry: SkillRegistry) -> None:
    session = make_session(class_="taoist", level=30)
    pet0 = summon_pet(session, "summon_skeleton", None, registry=registry)
    assert pet0.pet_type is PetType.skull
    assert pet0.hp == pet0.max_hp == balance.SKULL_BASE_HP
    session.skill_states["summon_skeleton"] = SkillState(
        skill_id="summon_skeleton", level=2
    )
    pet2 = summon_pet(session, "summon_skeleton", None, registry=registry)
    assert pet2.hp == balance.SKULL_BASE_HP + balance.SKULL_GROWTH_PER_LEVEL * 2
    assert pet2.skill_level == 2
    # 攻击力来自 fixture effect_params
    assert (pet2.attack_min, pet2.attack_max) == (5, 15)


def test_dog_hp_scales_with_skill_level(registry: SkillRegistry) -> None:
    session = make_session(class_="taoist", level=35)
    pet0 = summon_pet(session, "summon_beast", None, registry=registry)
    assert pet0.pet_type is PetType.dog
    assert pet0.hp == pet0.max_hp == balance.DOG_BASE_HP
    session.skill_states["summon_beast"] = SkillState(skill_id="summon_beast", level=3)
    pet3 = summon_pet(session, "summon_beast", None, registry=registry)
    assert pet3.hp == balance.DOG_BASE_HP + balance.DOG_GROWTH_PER_LEVEL * 3
    assert (pet3.attack_min, pet3.attack_max) == (15, 35)


# --- attack_target：击杀经验归主人 -------------------------------------------------


def test_pet_kill_exp_goes_to_owner(registry: SkillRegistry) -> None:
    session = make_session(class_="taoist", level=35, exp=100)
    monster_def = make_monster_def("shimu", hp=5, exp=77)
    zone, monsters = make_zone(monster_def)
    session.zone = zone
    pet = summon_pet(session, "summon_beast", None, registry=registry)
    target = monsters[0]
    # 循环出手直至击杀（seed 固定，必在有限步内命中）
    for _ in range(50):
        if target.instance_id not in {m.instance_id for m in zone.alive_monsters}:
            break
        attack_target(pet, target)
    assert not zone.alive_monsters, "目标应被 pet 击杀"
    assert session.char_snapshot.exp == 100 + 77
    assert pet.exp_yield_to_owner == 77
    names = event_names(session)
    assert "exp" in names
    exp_event = next(e for e in session.sse_state.buffer.after(0) if e.event == "exp")
    payload = json.loads(exp_event.data)
    assert payload["by"] == "pet"
    assert payload["amount"] == 77
    assert payload["total"] == 100 + 77


def test_attack_noop_after_owner_teardown(registry: SkillRegistry) -> None:
    """teardown（disperse_all → release_session）后 pet 出手静默为 0。"""
    session = make_session(class_="taoist", level=35)
    _, monsters = make_zone(make_monster_def())
    pet = summon_pet(session, "summon_beast", None, registry=registry)
    disperse_all(session)
    release_session(session.character_id)
    assert attack_target(pet, monsters[0]) == 0


# --- 生命周期：disperse / 断线宽限期后消失 ------------------------------------------


def test_disperse_single_pet(registry: SkillRegistry) -> None:
    session = make_session(class_="taoist", level=35)
    pet = summon_pet(session, "summon_beast", None, registry=registry)
    assert disperse(pet) is True
    assert pets_of(session) == []
    assert "pet_dispersed" in event_names(session)
    # 重复 disperse 幂等返回 False
    assert disperse(pet) is False


def test_pets_vanish_after_owner_release(registry: SkillRegistry) -> None:
    """主人断线宽限期后狗消失：模拟 teardown（disperse_all → release_session）。"""
    session = make_session(class_="taoist", level=35)
    summon_pet(session, "summon_beast", None, registry=registry)
    summon_pet(session, "summon_skeleton", None, registry=registry)
    assert len(pets_of(session)) == 2
    dispersed = disperse_all(session)
    assert dispersed == 2
    assert session.pets == []
    release_session(session.character_id)
    assert session_for(session.character_id) is None
    assert event_names(session).count("pet_dispersed") == 2


# --- tick_pets：攻击主人目标 / 目标切换 / 死亡 --------------------------------------


def test_tick_pets_attacks_owner_target(registry: SkillRegistry) -> None:
    session = make_session(class_="taoist", level=35)
    monster_def = make_monster_def("shimu", hp=10_000)
    zone, monsters = make_zone(monster_def)
    session.zone = zone
    pet = summon_pet(session, "summon_beast", None, registry=registry)
    alive = tick_pets(session, 1)
    assert alive == [pet]
    assert pet.target_monster_id == monsters[0].instance_id
    hp_after = session.monster_hp.get(monsters[0].instance_id, monster_def.hp)
    assert hp_after < monster_def.hp, "pet 应对主人目标造成伤害"


def test_tick_pets_switches_target_when_slain(registry: SkillRegistry) -> None:
    session = make_session(class_="taoist", level=35)
    d1 = make_monster_def("m1", hp=10_000)
    d2 = make_monster_def("m2", hp=10_000)
    zone, monsters = make_zone(d1, d2)
    session.zone = zone
    pet = summon_pet(session, "summon_beast", None, registry=registry)
    tick_pets(session, 1)
    assert pet.target_monster_id == monsters[0].instance_id
    # 模拟主人击杀目标怪（zone.kill_monster 移除 + HP 记账清除）
    zone.kill_monster(monsters[0].instance_id)
    session.monster_hp.pop(monsters[0].instance_id, None)
    tick_pets(session, 1)
    assert pet.target_monster_id == monsters[1].instance_id
    assert session.monster_hp.get(monsters[1].instance_id, d2.hp) < d2.hp


def test_tick_pets_idle_when_no_monster(registry: SkillRegistry) -> None:
    session = make_session(class_="taoist", level=35)
    zone, _ = make_zone()
    session.zone = zone
    pet = summon_pet(session, "summon_beast", None, registry=registry)
    alive = tick_pets(session, 1)
    assert alive == [pet]
    assert pet.target_monster_id is None
    assert session.monster_hp == {}


def test_tick_pets_removes_dead_pet(registry: SkillRegistry) -> None:
    session = make_session(class_="taoist", level=35)
    pet = summon_pet(session, "summon_beast", None, registry=registry)
    pet.hp = 0
    alive = tick_pets(session, 1)
    assert alive == []
    assert pets_of(session) == []
    assert "pet_died" in event_names(session)
    # 非主人所杀：主人经验不变
    assert session.char_snapshot.exp == 0


def test_tick_pets_rejects_negative_dt() -> None:
    session = make_session(class_="taoist", level=35)
    with pytest.raises(ValueError, match="dt_ticks"):
        tick_pets(session, -1)


# --- 数值纪律 ----------------------------------------------------------------------


def test_pet_module_has_no_decimal_literals() -> None:
    """pet.py 禁止 0.x 小数字面量（系数唯一来源 balance 的 grep 防回归纪律）。"""
    source = (
        Path(__file__).resolve().parent.parent.parent / "app" / "sim" / "pet.py"
    ).read_text(encoding="utf-8")
    violations = [
        lineno
        for lineno, line in enumerate(source.splitlines(), start=1)
        if DECIMAL_RE.search(line)
    ]
    assert not violations, f"pet.py 出现小数字面量: {violations}"
