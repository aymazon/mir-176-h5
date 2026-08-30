"""sim/scene.py 测试（battle-scene-ui todo 1）：场景快照 payload 纯函数。

覆盖：
- session.zone 为 None → monsters=[]（map_id 空串）；
- 已进图但无活怪 → monsters=[]、map_id 保留；
- 同名多实例：每只实例独立行（id=instance_id），仅首个活怪 is_target=True；
- monster_hp 无记账时 hp 回落 monster_def.hp，有记账时用记账值；
- boss 标记来自 monster_def.boss；
- pet 序列化：id/type(PetType.value)/name(monster_def.name)/hp/max_hp；
- 纯函数纪律：读取不污染 session.monster_hp（区别于 tick_loop 的 setdefault）。

纯内存用例：不碰 DB（无 engine.dispose 需求）；zone 用手工 ZoneInstance
+ 合成 SpawnEntry，同 test_pet.py 纪律。
"""

import random
from collections.abc import AsyncIterator

import pytest

from app.gamedata_schema import MonsterDef, SpawnEntry, SpawnLocation
from app.sim.pet import Pet, PetType
from app.sim.scene import build_scene_payload
from app.sim.session import (
    CharacterSnapshot,
    PlayerSession,
    max_hp_for,
    max_mp_for,
    register_session,
)
from app.sim.session import reset_sessions as reset_player_sessions
from app.sim.zone import ZoneInstance, shutdown_all
from app.sse.session_state import get_or_create_session
from app.sse.session_state import reset_sessions as reset_sse_sessions


@pytest.fixture(autouse=True)
async def _clean_runtime() -> AsyncIterator[None]:
    reset_player_sessions()
    reset_sse_sessions()
    await shutdown_all()
    yield
    reset_player_sessions()
    reset_sse_sessions()
    await shutdown_all()


def make_session(
    character_id: int = 1,
    *,
    class_: str = "wizard",
    level: int = 20,
) -> PlayerSession:
    """纯内存会话（假地图 → 不碰 DB）；固定 seed 复现随机流。"""
    snap = CharacterSnapshot(
        name=f"t{character_id}",
        class_=class_,
        level=level,
        exp=0,
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
    name: str | None = None,
    level: int = 10,
    hp: int = 20,
    attack: int = 5,
    boss: bool = False,
    exp: int = 50,
) -> MonsterDef:
    return MonsterDef(
        id=monster_id,
        name=name if name is not None else monster_id,
        level=level,
        hp=hp,
        attack=attack,
        defense=0,
        magic_defense=0,
        accuracy=5,
        agility=0,
        exp=exp,
        attack_speed=1,
        move_speed=0,
        undead=False,
        boss=boss,
        respawn_seconds=None,
    )


def make_zone(
    *defs: MonsterDef, map_id: str = "test-map", density: int = 10
) -> ZoneInstance:
    """手工 zone：每个 def 一个刷怪点（不走 enter_map/DB）；活怪由用例显式 spawn。"""
    table: dict[str, SpawnEntry] = {}
    for d in defs:
        table[f"{map_id}:{d.id}"] = SpawnEntry(
            map_id=map_id,
            monster_id=d.id,
            density=density,
            respawn_seconds=60,
            count=1,
            locations=[SpawnLocation(x=0, y=0)],
        )
    return ZoneInstance(
        map_id=map_id,
        static_spawn_table=table,
        monster_defs={d.id: d for d in defs},
    )


def make_pet(
    session: PlayerSession,
    *,
    pet_id: str = "pet-1",
    pet_type: PetType = PetType.dog,
    name: str = "神兽",
    hp: int = 80,
    max_hp: int = 100,
) -> Pet:
    pet = Pet(
        pet_id=pet_id,
        owner_character_id=session.character_id,
        pet_type=pet_type,
        monster_def=make_monster_def(f"def-{pet_id}", name=name, hp=max_hp),
        skill_id="summon_beast",
        skill_level=1,
        hp=hp,
        max_hp=max_hp,
        attack_min=5,
        attack_max=10,
    )
    session.pets.append(pet)
    return pet


# --- 空场景 -----------------------------------------------------------------


def test_no_zone_yields_empty_monsters_and_blank_map_id() -> None:
    session = make_session()
    assert session.zone is None
    assert build_scene_payload(session) == {"map_id": "", "monsters": [], "pets": []}


def test_zone_without_alive_monsters_keeps_map_id() -> None:
    session = make_session()
    monster_def = make_monster_def()
    session.zone = make_zone(monster_def, map_id="miren-1")
    payload = build_scene_payload(session)
    assert payload["map_id"] == "miren-1"
    assert payload["monsters"] == []
    assert payload["pets"] == []


# --- 怪物行 -----------------------------------------------------------------


def test_same_name_instances_each_get_own_row_and_single_target() -> None:
    """同名（同 def）多实例：各自一行（id=instance_id），仅首个活怪 is_target。"""
    session = make_session()
    monster_def = make_monster_def("orc", name="半兽战士", hp=30)
    zone = make_zone(monster_def, density=2)
    session.zone = zone
    first = zone.spawn_monster(monster_def, zone.static_spawn_table["test-map:orc"])
    second = zone.spawn_monster(monster_def, zone.static_spawn_table["test-map:orc"])

    payload = build_scene_payload(session)

    rows = payload["monsters"]
    assert len(rows) == 2
    assert rows[0]["id"] == first.instance_id
    assert rows[1]["id"] == second.instance_id
    assert rows[0]["id"] != rows[1]["id"]
    assert [row["name"] for row in rows] == ["半兽战士", "半兽战士"]
    assert [row["monster_id"] for row in rows] == ["orc", "orc"]
    assert [row["is_target"] for row in rows] == [True, False]


def test_hp_falls_back_to_def_hp_then_uses_monster_hp_ledger() -> None:
    session = make_session()
    monster_def = make_monster_def("shimu", hp=50)
    zone = make_zone(monster_def)
    session.zone = zone
    inst = zone.spawn_monster(monster_def, zone.static_spawn_table["test-map:shimu"])

    payload = build_scene_payload(session)
    row = payload["monsters"][0]
    assert row["hp"] == 50
    assert row["max_hp"] == 50

    session.monster_hp[inst.instance_id] = 7
    payload = build_scene_payload(session)
    row = payload["monsters"][0]
    assert row["hp"] == 7
    assert row["max_hp"] == 50


def test_snapshot_read_does_not_pollute_monster_hp_ledger() -> None:
    """纯函数纪律：快照读取不得像 tick_loop._monster_hp 那样 setdefault 入账。"""
    session = make_session()
    monster_def = make_monster_def("shimu", hp=50)
    zone = make_zone(monster_def)
    session.zone = zone
    zone.spawn_monster(monster_def, zone.static_spawn_table["test-map:shimu"])

    build_scene_payload(session)
    assert session.monster_hp == {}


def test_boss_flag_from_monster_def() -> None:
    session = make_session()
    boss_def = make_monster_def("wooma_taurus", name="沃玛教主", hp=500, boss=True)
    normal_def = make_monster_def("shimu", hp=50)
    zone = make_zone(boss_def, normal_def)
    session.zone = zone
    zone.spawn_monster(boss_def, zone.static_spawn_table["test-map:wooma_taurus"])
    zone.spawn_monster(normal_def, zone.static_spawn_table["test-map:shimu"])

    payload = build_scene_payload(session)
    assert [row["boss"] for row in payload["monsters"]] == [True, False]


# --- 召唤物行 ----------------------------------------------------------------


def test_pets_serialized_with_enum_type_value_and_def_name() -> None:
    session = make_session()
    make_pet(session, pet_id="pet-dog", pet_type=PetType.dog, name="神兽", hp=80, max_hp=100)
    make_pet(
        session,
        pet_id="pet-charm",
        pet_type=PetType.charm,
        name="半兽战士",
        hp=30,
        max_hp=30,
    )

    payload = build_scene_payload(session)

    assert payload["pets"] == [
        {"id": "pet-dog", "type": "dog", "name": "神兽", "hp": 80, "max_hp": 100},
        {
            "id": "pet-charm",
            "type": "charm",
            "name": "半兽战士",
            "hp": 30,
            "max_hp": 30,
        },
    ]


def test_full_frame_shape() -> None:
    """整帧形状：map_id + monsters + pets 三键齐备，可 JSON 序列化语义。"""
    session = make_session()
    monster_def = make_monster_def("orc", name="半兽战士", hp=30)
    zone = make_zone(monster_def, map_id="miren-2")
    session.zone = zone
    inst = zone.spawn_monster(monster_def, zone.static_spawn_table["miren-2:orc"])
    session.monster_hp[inst.instance_id] = 12
    make_pet(session, pet_id="pet-skull", pet_type=PetType.skull, name="骷髅", hp=40, max_hp=60)

    payload = build_scene_payload(session)

    assert set(payload) == {"map_id", "monsters", "pets"}
    assert payload["map_id"] == "miren-2"
    assert payload["monsters"] == [
        {
            "id": inst.instance_id,
            "monster_id": "orc",
            "name": "半兽战士",
            "hp": 12,
            "max_hp": 30,
            "boss": False,
            "is_target": True,
        }
    ]
    assert payload["pets"] == [
        {"id": "pet-skull", "type": "skull", "name": "骷髅", "hp": 40, "max_hp": 60}
    ]
