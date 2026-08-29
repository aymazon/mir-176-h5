"""Gamedata lint 规则（todo 19 起步；todo 22 全量门禁 + --strict 接入）。

规则与 gamedata_loader 的结构校验解耦：loader 管「形状/引用合法性」，
lint 管「数值语义合理性 + 门禁级交叉复核」。每条规则是纯函数，输入已加载
数据索引（或 gamedata 根目录），输出问题清单（文件相对路径 + 字段 + 原因），
供 gamedata_cli validate --strict 统一调用，也可在单测中独立调用。

todo 22 规则集：
1. lint_cross_refs_integrity —— id 引用完整性（drops→items、spawns→monsters/maps、
   quests→按 kind 定向、maps/npcs 引用），报错含文件 + 字段路径 + 期望存在类别；
2. lint_birth_map_exists —— config.BIRTH_MAP_ID 必须存在于 maps；
3. lint_monster_level_in_band —— 怪物 level ∈ 地图等级带 ±3（BOSS 豁免 +
   已评审例外登记表，理由见 LEVEL_BAND_EXCEPTIONS 与 docs/data-coverage.md）；
4. lint_weapon_level_sequence —— 以 README §7 战士武器序列为基线做 diff 断言：
   仅当「基线锚点武器」的 need_level 与文档冲突时硬错；基线外新武器只进
   diff 报告不报错（禁用"等差无断档"与"新项即错"两类误规则）；
5. lint_id_only_references —— gamedata 全部 JSON 中，除自由文本字段白名单外
   不允许出现中文（跨类别引用只能走 id，不能走中文名）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .core.config import BIRTH_MAP_ID
from .gamedata_schema import (
    DropEntry,
    MapDef,
    MonsterDef,
    NpcDef,
    QuestDef,
    QuestKind,
    SpawnEntry,
)

if TYPE_CHECKING:
    from .gamedata_schema.items import ItemDef

# ---------------------------------------------------------------------------
# todo 19：新手装门槛（原规则，未改动）
# ---------------------------------------------------------------------------

# 新手装词缀 → 门槛上限。命中词缀的 weapon/armor 必须 need_level ≤ 上限，
# 否则视为录入错误（如粗布袍误录 need_level=99）。
# task 38 换皮后词缀同步为新名（映射见 docs/reskin-mapping.md，上限数值不变）。
NEWBIE_EQUIPMENT_RULES: tuple[tuple[str, int], ...] = (
    ("粗布袍", 15),
    ("木刃", 15),
    ("黑檀剑", 15),
    ("铁刃剑", 15),
    ("短匕", 15),
    ("铜纹剑", 15),
    ("轻装皮甲", 15),
)

_NEWBIE_KINDS = frozenset({"weapon", "armor"})


def lint_newbie_equipment_thresholds(
    items: dict[str, ItemDef],
) -> list[str]:
    """新手装门槛异常 lint。

    任何 kind ∈ {weapon, armor} 且 name 命中 NEWBIE_EQUIPMENT_RULES 词缀的物品，
    need_level 超过对应上限即报错；报错格式对齐 loader：
    ``items/<id>.json: need_level <n> exceeds newbie cap <cap> (name match '<affix>')``。
    返回空列表 = 通过。
    """
    problems: list[str] = []
    for item_id in sorted(items):
        item = items[item_id]
        if item.kind not in _NEWBIE_KINDS:
            continue
        for affix, cap in NEWBIE_EQUIPMENT_RULES:
            if affix not in item.name:
                continue
            if item.need_level > cap:
                detail = (
                    f"items/{item_id}.json: need_level {item.need_level} exceeds "
                    f"newbie cap {cap} (name match '{affix}')"
                )
                problems.append(detail)
            break  # 一件物品只报第一条命中词缀，避免重复计数
    return problems


# ---------------------------------------------------------------------------
# todo 22 规则 1：id 引用完整性
# ---------------------------------------------------------------------------

# quests.target_id 按 kind 定向到被引用类别（与 loader._QUEST_TARGET 同语义）。
_QUEST_TARGET: dict[QuestKind, str] = {
    QuestKind.kill: "monsters",
    QuestKind.collect: "items",
    QuestKind.dialogue: "npcs",
    QuestKind.profession: "skills",
}


def _default_rel(category: str, key: str) -> str:
    """origins 缺省时的约定路径：id 键类别 <id>.json；drops/spawns 按命名惯例。"""
    if category == "drops":
        return f"drops/{key}.json"
    if category == "spawns":
        map_id, _, monster_id = key.partition(":")
        return f"spawns/{map_id}_{monster_id}.json"
    return f"{category}/{key}.json"


def _ref_check(
    problems: list[str],
    rel: str,
    field_path: str,
    value: str,
    target_category: str,
) -> None:
    detail = (
        f"{rel}: {field_path} '{value}' not found in {target_category}"
        f" (dangling id reference)"
    )
    problems.append(detail)


def lint_cross_refs_integrity(
    data: Mapping[str, Mapping[str, object]],
    origins: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """id 引用完整性 lint（todo 22）：一切跨类别引用必须走已存在的 id。

    覆盖 loader._check_cross_references 的全部引用族（独立重实现，
    供 strict 门禁二次复核与合成数据单测直接调用）：
    - maps: adjacent → maps、boss_spawns → monsters
    - npcs: map_id → maps、shop_items/shop_entries[].item_id → items
    - drops: monster_id → monsters、entries[].item_id(非 null) → items
    - spawns: map_id → maps、monster_id → monsters
    - quests: target_id 按 kind 定向、reward_items → items

    origins 为 loader 的 key→来源文件映射；缺省时按命名惯例推导相对路径。
    返回空列表 = 通过。
    """

    def rel(category: str, key: str) -> str:
        if origins is not None and category in origins:
            return origins[category].get(key, _default_rel(category, key))
        return _default_rel(category, key)

    problems: list[str] = []
    maps = cast("dict[str, MapDef]", data["maps"])
    monsters = cast("dict[str, MonsterDef]", data["monsters"])
    items = cast("dict[str, ItemDef]", data["items"])
    skills = cast("dict[str, object]", data["skills"])
    npcs = cast("dict[str, NpcDef]", data["npcs"])
    quests = cast("dict[str, QuestDef]", data["quests"])
    drops = cast("dict[str, DropEntry]", data["drops"])
    spawns = cast("dict[str, SpawnEntry]", data["spawns"])

    # maps: adjacent → maps，boss_spawns → monsters
    for key, m in maps.items():
        for i, ref in enumerate(m.adjacent):
            if ref not in maps:
                _ref_check(problems, rel("maps", key), f"adjacent[{i}]", ref, "maps")
        for i, ref in enumerate(m.boss_spawns):
            if ref not in monsters:
                _ref_check(
                    problems, rel("maps", key), f"boss_spawns[{i}]", ref, "monsters"
                )

    # npcs: map_id → maps，shop_items / shop_entries[].item_id → items
    for key, n in npcs.items():
        if n.map_id not in maps:
            _ref_check(problems, rel("npcs", key), "map_id", n.map_id, "maps")
        for i, ref in enumerate(n.shop_items):
            if ref not in items:
                _ref_check(problems, rel("npcs", key), f"shop_items[{i}]", ref, "items")
        for i, entry in enumerate(n.shop_entries):
            if entry.item_id not in items:
                _ref_check(
                    problems,
                    rel("npcs", key),
                    f"shop_entries[{i}].item_id",
                    entry.item_id,
                    "items",
                )

    # drops: monster_id → monsters，entries[].item_id(非 null) → items
    for key, d in drops.items():
        if d.monster_id not in monsters:
            _ref_check(
                problems, rel("drops", key), "monster_id", d.monster_id, "monsters"
            )
        for i, entry in enumerate(d.entries):
            if entry.item_id is not None and entry.item_id not in items:
                _ref_check(
                    problems,
                    rel("drops", key),
                    f"entries[{i}].item_id",
                    entry.item_id,
                    "items",
                )

    # spawns: map_id → maps，monster_id → monsters
    for key, s in spawns.items():
        if s.map_id not in maps:
            _ref_check(problems, rel("spawns", key), "map_id", s.map_id, "maps")
        if s.monster_id not in monsters:
            _ref_check(
                problems, rel("spawns", key), "monster_id", s.monster_id, "monsters"
            )

    # quests: target_id 按 kind 定向，reward_items → items
    quest_indexes: dict[str, dict[str, object]] = {
        "monsters": cast("dict[str, object]", monsters),
        "items": cast("dict[str, object]", items),
        "npcs": cast("dict[str, object]", npcs),
        "skills": skills,
    }
    for key, q in quests.items():
        target_category = _QUEST_TARGET[q.kind]
        if q.target_id not in quest_indexes[target_category]:
            _ref_check(
                problems,
                rel("quests", key),
                "target_id",
                q.target_id,
                target_category,
            )
        for i, ref in enumerate(q.reward_items):
            if ref not in items:
                _ref_check(
                    problems, rel("quests", key), f"reward_items[{i}]", ref, "items"
                )

    return problems


# ---------------------------------------------------------------------------
# todo 22 规则 2：出生地图存在性
# ---------------------------------------------------------------------------


def lint_birth_map_exists(maps: dict[str, MapDef]) -> list[str]:
    """config.BIRTH_MAP_ID 引用的地图必须存在于 maps（todo 22）。

    报错格式：``maps/: BIRTH_MAP_ID '<id>' not found in maps (app.core.config)``。
    """
    if BIRTH_MAP_ID in maps:
        return []
    detail = f"maps/: BIRTH_MAP_ID '{BIRTH_MAP_ID}' not found in maps (app.core.config)"
    return [detail]


# ---------------------------------------------------------------------------
# todo 22 规则 3：等级带与地图匹配
# ---------------------------------------------------------------------------

# 等级带容差：map.level_min - TOL <= monster.level <= map.level_max + TOL
_TOL_BAND = 3

# 已评审例外登记表（"map_id:monster_id" → 理由）。BOSS（boss=True）一律豁免
# ——经典 176 设计即"低级图藏高级 BOSS"（尸王洞/触龙神/沃玛教主等），无需登记。
# 非 BOSS 例外均为双源录入后人工复核过的经典布怪形态，理由同步记录在
# docs/data-coverage.md §11.4；新增越带怪不在表内即 strict 硬错（防误放闸门）。
LEVEL_BAND_EXCEPTIONS: dict[str, str] = {
    "biqi-mine:bat": "矿区入口过渡怪（L5，经典矿区外围低级怪）",
    "biqi-mine:jumping_bee": "矿区深层怪（L26，玩家入矿后深层遭遇）",
    "biqi-mine:lightning_zombie": "僵尸洞深层怪（L34，矿区即僵尸洞入口）",
    "biqi-mine:zombie": "僵尸洞主怪（L25，经典僵尸洞位于矿区深层）",
    "biqi-mine:zombie_fighter": "僵尸洞深层怪（L28）",
    "biqi-wild:man_eating_flower": "比奇野外边缘杂怪（L20，野外南缘高级草怪）",
    "mengzhong:horn_fly": "盟重过渡怪（L30，盟重外围低级飞怪）",
    "shouren-gu-3:orc": "半兽人贯穿三层（L15 为同族低层怪延续到三层入口）",
    "snake-valley:two_head_snake": "蛇谷深层怪（L26，谷底深洞遭遇）",
    "snake-valley:two_head_snake_demon": "蛇谷深处蛇魔（L30，mini-boss 布怪）",
    "wooma-forest:ancient_wooma_warrior": "远古沃玛勇士为森林守卫级精英（L53）",
    "wooma-forest:forest_yeti": "森林边缘过渡怪（L16，沃玛森林外围低级怪）",
    "wooma-forest:poison_spider": "森林边缘过渡怪（L16，沃玛森林外围低级怪）",
    "wooma-temple:wooma_guardian": "沃玛卫士为寺庙守卫级怪（L50，教主前厅护卫）",
    "zuma-caves:zuma_guardian": "祖玛卫士为寺庙守卫级怪（L50，教主前厅护卫）",
    "zuma-caves:zuma_guardian_elite": "极品祖玛卫士（L50，寺庙精英护卫）",
    "zuma-caves:zuma_statue": "祖玛雕像（L50，寺庙机关怪）",
}


def lint_monster_level_in_band(
    spawns: dict[str, SpawnEntry],
    monsters: dict[str, MonsterDef],
    maps: dict[str, MapDef],
) -> list[str]:
    """怪物 level 必须落在所在地图等级带 ±3 内（todo 22）。

    BOSS（boss=True）豁免：经典设计低级图藏高级 BOSS。其余越带怪仅当命中
    LEVEL_BAND_EXCEPTIONS 登记表（附理由）才放行，未登记即硬错——防新怪误放。
    报错格式：``spawns/<map>_<monster>.json: monster '<id>' level <n> outside
    band [<lo>, <hi>] of map '<map_id>' (<band_min>-<band_max>)``。
    """
    problems: list[str] = []
    for key in sorted(spawns):
        spawn = spawns[key]
        monster = monsters.get(spawn.monster_id)
        map_def = maps.get(spawn.map_id)
        if monster is None or map_def is None:
            continue  # 悬空引用由规则 1 负责，此处跳过避免重复报错
        if monster.boss:
            continue
        lo = map_def.level_min - _TOL_BAND
        hi = map_def.level_max + _TOL_BAND
        if lo <= monster.level <= hi:
            continue
        exception_key = f"{spawn.map_id}:{spawn.monster_id}"
        if exception_key in LEVEL_BAND_EXCEPTIONS:
            continue
        detail = (
            f"spawns/{spawn.map_id}_{spawn.monster_id}.json: "
            f"monster '{spawn.monster_id}' level {monster.level} outside band "
            f"[{lo}, {hi}] of map '{spawn.map_id}' "
            f"({map_def.level_min}-{map_def.level_max})"
        )
        problems.append(detail)
    return problems


# ---------------------------------------------------------------------------
# todo 22 规则 4：装备等级序列（README §7 基线 diff 断言）
# ---------------------------------------------------------------------------

# docs/MVP/README.md §7.1 战士武器进阶序列（名称 → need_level）。
# 这是"基线锚点"而非穷举：基线内武器等级漂移 = 硬错；基线外新武器
# （屠龙/命运之刃/血饮等，task 38 换皮后 = 斩龙刃/淬锋之刃/饮血剑）只进 diff 报告，
# 不算错误。锚点名称随 task 38 换皮同步为新名（映射见 docs/reskin-mapping.md，
# need_level 数值不变）。
WARRIOR_WEAPON_BASELINE: tuple[tuple[str, int], ...] = (
    ("黑檀剑", 1),
    ("铜纹剑", 5),
    ("铁刃剑", 10),
    ("拓荒刀", 15),
    ("逐风剑", 19),
    ("断岳刀", 20),
    ("戮战刀", 22),
    ("覆霜剑", 25),
    ("熔狱刀", 26),
    ("潭月刀", 28),
    ("断罪之杖", 30),
)


def lint_weapon_level_sequence(items: dict[str, ItemDef]) -> list[str]:
    """战士武器序列与 README §7 基线的 diff 断言（todo 22）。

    只对「名字命中基线」的 weapon 断言 need_level 一致（锚点防漂移）；
    基线外的武器不做任何断言（既不断言等差无断档，也不断言新项必错）。
    报错格式：``items/<id>.json: warrior baseline drift: '<name>' need_level
    <actual> != doc §7 baseline <expected>``。
    """
    baseline = dict(WARRIOR_WEAPON_BASELINE)
    problems: list[str] = []
    for item_id in sorted(items):
        item = items[item_id]
        if item.kind.value != "weapon":
            continue
        expected = baseline.get(item.name)
        if expected is None:
            continue
        if item.need_level != expected:
            detail = (
                f"items/{item_id}.json: warrior baseline drift: "
                f"'{item.name}' need_level {item.need_level} != "
                f"doc §7 baseline {expected}"
            )
            problems.append(detail)
    return problems


def weapon_level_sequence_report(items: dict[str, ItemDef]) -> list[str]:
    """战士武器序列 diff 报告（信息性，不影响退出码；strict 时打印 stdout）。

    输出实际序列 vs 基线：命中锚点 OK/DRIFT、基线外 EXTRA、缺失 MISSING。
    """
    baseline = dict(WARRIOR_WEAPON_BASELINE)
    weapons = sorted(
        (it for it in items.values() if it.kind.value == "weapon"),
        key=lambda it: (it.need_level, it.id),
    )
    lines = ["### 战士武器序列 diff（实际 vs README §7 基线）"]
    matched_names: set[str] = set()
    for w in weapons:
        expected = baseline.get(w.name)
        if expected is not None:
            mark = "OK" if w.need_level == expected else "DRIFT"
            matched_names.add(w.name)
            lines.append(f"- [{mark}] {w.name}({w.need_level}) id={w.id}")
        else:
            lines.append(f"- [EXTRA] {w.name}({w.need_level}) id={w.id}（基线外）")
    for name, _lv in WARRIOR_WEAPON_BASELINE:
        if name not in matched_names:
            lines.append(f"- [MISSING] {name}（基线锚点未录入）")
    return lines


# ---------------------------------------------------------------------------
# todo 22 规则 5：gamedata 内只走 id（无名字交叉引用）
# ---------------------------------------------------------------------------

# 自由文本字段白名单：这些键的子树允许中文（展示文案/出处注释）。
_FREE_TEXT_KEYS = frozenset(
    {"name", "region", "dialogue", "description", "display_name", "comment"}
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _scan_cjk(obj: object, path: str, rel: str, problems: list[str]) -> None:
    if isinstance(obj, dict):
        entries = cast("dict[object, object]", obj)
        for key, value in entries.items():
            key_str = str(key)
            child_path = f"{path}.{key_str}" if path else key_str
            if _CJK_RE.search(key_str):
                detail = (
                    f"{rel}: {child_path}: dict key contains CJK characters "
                    f"(cross-category references must use ids)"
                )
                problems.append(detail)
            if key_str in _FREE_TEXT_KEYS:
                continue  # 自由文本子树整体豁免
            _scan_cjk(value, child_path, rel, problems)
    elif isinstance(obj, list):
        items = cast("list[object]", obj)
        for i, value in enumerate(items):
            _scan_cjk(value, f"{path}[{i}]", rel, problems)
    elif isinstance(obj, str) and _CJK_RE.search(obj):
        detail = (
            f"{rel}: {path}: value '{obj}' contains CJK characters "
            f"(cross-category references must use ids, not names)"
        )
        problems.append(detail)


def lint_id_only_references(root: Path) -> list[str]:
    """gamedata 全部 JSON 只走 id 引用（todo 22，git grep 断言的结构化实现）。

    遍历 root 下所有 *.json：除自由文本字段白名单（name/region/dialogue/
    description/display_name/comment）外，任何字符串值/字典键出现中文即报错
    ——id 均为 ASCII slug，中文出现在引用位即"名字交叉引用"录入错误。
    相比 ``git grep`` 子进程：对 tmp_path 合成目录同样可用（测试友好）、
    逐字段定位精确到路径。报错格式：``<rel>: <field path>: value '...' contains
    CJK characters (...)``。
    """
    problems: list[str] = []
    for path in sorted(root.rglob("*.json")):
        rel = str(path.relative_to(root))
        try:
            raw = cast("object", json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue  # JSON 语法错误由 loader 负责
        _scan_cjk(raw, "", rel, problems)
    return problems


__all__ = [
    "NEWBIE_EQUIPMENT_RULES",
    "WARRIOR_WEAPON_BASELINE",
    "lint_birth_map_exists",
    "lint_cross_refs_integrity",
    "lint_id_only_references",
    "lint_monster_level_in_band",
    "lint_newbie_equipment_thresholds",
    "lint_weapon_level_sequence",
    "weapon_level_sequence_report",
]
