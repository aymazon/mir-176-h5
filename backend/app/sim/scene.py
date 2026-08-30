"""战斗场景快照 payload 构建（battle-scene-ui todo 1）。

职责边界（计划红线）：
- **纯函数、只读**：从 ``PlayerSession`` 内存态组装前端战斗场景的一帧快照
  （map_id + 场上怪物行 + 召唤物行）。不碰 DB、不发 SSE、不写日记、
  不改任何会话状态（``monster_hp`` 只 ``get`` 不 ``setdefault``——区别于
  ``tick_loop._monster_hp`` 的首见记账语义，快照读取不得污染战斗账）。
- HP 事实源：``MonsterInstance`` 不携带血量，当前 HP 记账在
  ``session.monster_hp``（instance_id → 剩余 HP）；未见过的怪回落
  ``monster_def.hp``（满血），与 tick_loop 首见初始化语义一致。
- 目标约定：``is_target`` 仅 ``zone.alive_monsters[0]`` 为 True——与
  tick_loop / pet AI 的"玩家目标 = 首个活怪"约定同源。
"""

from __future__ import annotations

from typing import TypedDict

from app.sim.session import PlayerSession

__all__ = [
    "MonsterRow",
    "PetRow",
    "ScenePayload",
    "build_scene_payload",
]


class MonsterRow(TypedDict):
    """场上怪物行：id 为实例 id（同名多实例各自一行）。"""

    id: str
    monster_id: str
    name: str
    hp: int
    max_hp: int
    boss: bool
    is_target: bool


class PetRow(TypedDict):
    """召唤物行：type 为 PetType 的字符串值（charm/skull/dog）。"""

    id: str
    type: str
    name: str
    hp: int
    max_hp: int


class ScenePayload(TypedDict):
    """场景快照：zone 未进图时 map_id 为空串、monsters 为空表。"""

    map_id: str
    monsters: list[MonsterRow]
    pets: list[PetRow]


def build_scene_payload(session: PlayerSession) -> ScenePayload:
    """组装一帧战斗场景快照（纯内存只读，无副作用）。

    - ``session.zone`` 为 None 或区内无活怪 → ``monsters`` 空表；
    - 怪物 hp = ``session.monster_hp.get(instance_id, monster_def.hp)``，
      ``max_hp`` 恒为 ``monster_def.hp``；
    - ``is_target`` 仅首个活怪（``alive_monsters[0]``）为 True；
    - pet name 取 ``pet.monster_def.name``，type 取 ``pet.pet_type.value``。
    """
    zone = session.zone
    monsters: list[MonsterRow] = []
    if zone is not None:
        for index, inst in enumerate(zone.alive_monsters):
            monster_def = inst.monster_def
            monsters.append(
                {
                    "id": inst.instance_id,
                    "monster_id": inst.monster_id,
                    "name": monster_def.name,
                    "hp": session.monster_hp.get(inst.instance_id, monster_def.hp),
                    "max_hp": monster_def.hp,
                    "boss": bool(monster_def.boss),
                    "is_target": index == 0,
                }
            )
    pets: list[PetRow] = [
        {
            "id": pet.pet_id,
            "type": pet.pet_type.value,
            "name": pet.monster_def.name,
            "hp": pet.hp,
            "max_hp": pet.max_hp,
        }
        for pet in session.pets
    ]
    return {
        "map_id": zone.map_id if zone is not None else "",
        "monsters": monsters,
        "pets": pets,
    }
