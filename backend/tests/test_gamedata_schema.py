"""gamedata schema 与校验器测试（todo 4 / C3a）。

覆盖：8 类样例 happy path、3 个 negative（坏 JSON / 悬空 id / 缺必填字段）、
CLI subprocess 退出码。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from app.gamedata_lint import (
    lint_birth_map_exists,
    lint_cross_refs_integrity,
    lint_id_only_references,
    lint_monster_level_in_band,
    lint_newbie_equipment_thresholds,
    lint_weapon_level_sequence,
    weapon_level_sequence_report,
)
from app.gamedata_loader import GameDataError, load_all
from app.gamedata_schema import (
    DropEntry,
    ItemDef,
    LevelEntry,
    LevelTable,
    MapDef,
    MonsterDef,
    NpcDef,
    QuestDef,
    SkillDef,
    SpawnEntry,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent
GAMEDATA_ROOT = BACKEND_ROOT / "gamedata"


def _load_sample(category: str, stem: str) -> dict[str, Any]:
    return json.loads(
        (GAMEDATA_ROOT / category / f"{stem}.json").read_text(encoding="utf-8")
    )

# todo 19 抽查件：任务规格点名的 4 类 + 40 级新衣服三件
_ITEM_PROBES = (
    "verdict_staff",          # 裁决之杖
    "bone_jade_staff",        # 骨玉权杖
    "dragonvein_sword",       # 龙纹剑
    "sky_demon_armor",        # 天魔神甲（40 级新衣）
    "archmage_cloak",         # 法神披风（40 级新衣）
    "heavenly_taoist_robe",   # 天尊道袍（40 级新衣）
)


# ---------- 8 个 happy test：每个 schema 解析合法样例 OK ----------


def test_map_sample_ok():
    m = MapDef.model_validate(_load_sample("maps", "yin-xing-cun"))
    assert m.id == "yin-xing-cun"
    assert m.level_min <= m.level_max


def test_monster_sample_ok():
    m = MonsterDef.model_validate(_load_sample("monsters", "chicken"))
    assert m.id == "chicken"
    assert m.respawn_seconds == 60


def test_item_sample_ok():
    i = ItemDef.model_validate(_load_sample("items", "wooden_sword"))
    assert i.id == "wooden_sword"
    assert i.kind.value == "weapon"


def test_skill_sample_ok():
    s = SkillDef.model_validate(_load_sample("skills", "basic_sword"))
    assert s.id == "basic_sword"
    # JSON 键 "class" 经 alias 映射到 class_
    assert s.class_.value == "warrior"
    assert len(s.level_exp_required) == s.max_level


def test_npc_sample_ok():
    n = NpcDef.model_validate(_load_sample("npcs", "healer_npc"))
    assert n.map_id == "yin-xing-cun"
    # todo 20 起药店售卖小瓶药（§8 城市等级规则：银杏村=L1 城）
    assert n.shop_items == ["potion_hp_small", "potion_mp_small"]


def test_quest_sample_ok():
    q = QuestDef.model_validate(_load_sample("quests", "newbie_kill_5"))
    assert q.kind.value == "kill"
    assert q.target_id == "chicken"


def test_drop_sample_ok():
    d = DropEntry.model_validate(_load_sample("drops", "chicken_drops"))
    assert d.monster_id == "chicken"
    # 样例数据由 scripts/convert_gamedata.py（todo 13）生成，断言结构性质而非具体数值
    assert d.entries[0].item_id is None  # 金币掉落
    assert "wooden_sword" in {e.item_id for e in d.entries}


def test_spawn_sample_ok():
    s = SpawnEntry.model_validate(_load_sample("spawns", "yin-xing-cun_chicken"))
    assert s.map_id == "yin-xing-cun"
    # 样例数据由转换器生成：断言点位非空且坐标合法，不锁具体数值
    assert s.locations
    assert all(loc.x >= 0 and loc.y >= 0 for loc in s.locations)


def test_levels_sample_ok():
    """todo 17：levels.json（单文件类别）1-40 级经验表，主源 GeeM2 Exps.ini。"""
    t = LevelTable.model_validate(json.loads((GAMEDATA_ROOT / "levels.json").read_text(encoding="utf-8")))
    assert len(t.levels) == 40
    assert [e.level for e in t.levels] == list(range(1, 41))
    assert t.levels[0].exp_to_next > 0  # 1→2 级必须有正经验门槛
    assert t.levels[-1].exp_to_next == 0  # 40 级满级哨兵
    # 单条 LevelEntry 形状独立可用
    entry = LevelEntry.model_validate({"level": 7, "exp_to_next": 1200})
    assert entry.level == 7


def test_load_all_ok():
    data = load_all(GAMEDATA_ROOT)
    assert set(data) == {
        "maps", "monsters", "items", "skills", "npcs", "quests", "drops", "spawns",
        "levels", "teleport",
    }
    assert "yin-xing-cun" in data["maps"]
    assert "chicken" in data["drops"]
    assert "yin-xing-cun:chicken" in data["spawns"]
    assert set(data["levels"]) == {str(lv) for lv in range(1, 41)}


# ---------- 3 个 negative：报具体文件 + 字段路径 ----------


@pytest.fixture()
def broken_root(tmp_path: Path) -> Path:
    """把样例 gamedata 复制到 tmp，供破坏后校验。"""
    root = tmp_path / "gamedata"
    shutil.copytree(GAMEDATA_ROOT, root)
    return root


def test_bad_json_reports_file_and_line(broken_root: Path):
    target = broken_root / "monsters" / "chicken.json"
    target.write_text('{\n  "id": "chicken",\n  bad\n}', encoding="utf-8")
    with pytest.raises(GameDataError) as exc_info:
        load_all(broken_root)
    msg = str(exc_info.value)
    assert "monsters/chicken.json" in msg
    assert "line 3" in msg  # 第 3 行 "bad" 处语法错误


def test_dangling_id_reports_file_and_field(broken_root: Path):
    target = broken_root / "drops" / "chicken_drops.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["entries"][1]["item_id"] = "fake_item"
    target.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    with pytest.raises(GameDataError) as exc_info:
        load_all(broken_root)
    msg = str(exc_info.value)
    assert "drops/chicken_drops.json" in msg
    assert "entries[1].item_id" in msg
    assert "'fake_item' not found in items" in msg


def test_missing_required_field_reports_file_and_field(broken_root: Path):
    target = broken_root / "monsters" / "chicken.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    del raw["hp"]
    target.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    with pytest.raises(GameDataError) as exc_info:
        load_all(broken_root)
    msg = str(exc_info.value)
    assert "monsters/chicken.json" in msg
    assert "hp" in msg
    assert "Field required" in msg


def test_levels_out_of_order_reports_file_and_index(broken_root: Path):
    """levels.json 负例：等级不连续 → 报文件 + 列表下标。"""
    target = broken_root / "levels.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["levels"][5]["level"] = 99  # 第 6 条应为 level 6
    target.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    with pytest.raises(GameDataError) as exc_info:
        load_all(broken_root)
    msg = str(exc_info.value)
    assert "levels.json" in msg
    assert "levels[5]" in msg
    assert "out of order" in msg


# ---------- CLI subprocess ----------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.gamedata_cli", *args],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_validate_exit_0_on_samples():
    result = _run_cli("validate")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_cli_validate_exit_1_on_dangling_id(broken_root: Path):
    target = broken_root / "spawns" / "yin-xing-cun_chicken.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["map_id"] = "nowhere"
    target.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    result = _run_cli("validate", "--root", str(broken_root))
    assert result.returncode == 1
    assert "spawns/yin-xing-cun_chicken.json" in result.stderr
    assert "map_id 'nowhere' not found in maps" in result.stderr


# ---------- todo 19：物品全量数据 + 新手装门槛 lint ----------


def test_items_full_dataset_count():
    """物品总数 ≥120（task 19 验收）。"""
    count = len(list((GAMEDATA_ROOT / "items").glob("*.json")))
    assert count >= 120, f"items/ 仅 {count} 件 < 120"


def test_item_probes_exist_with_thresholds():
    """裁决之杖/骨玉权杖/龙纹剑/40 级新衣服三件存在且门槛字段非空。"""
    for stem in _ITEM_PROBES:
        item = ItemDef.model_validate(_load_sample("items", stem))
        thresholds = (
            item.need_level,
            item.need_strength,
            item.need_magic,
            item.need_taoism,
        )
        assert any(v > 0 for v in thresholds), f"{stem} 门槛字段全空"


def test_wuji_cudgel_need_level_28():
    """待定项裁决：浑元棍（旧名无极棍）按社区主流 28 级录入（置信度中）。"""
    item = ItemDef.model_validate(_load_sample("items", "wuji_cudgel"))
    assert item.name == "浑元棍"
    assert item.need_level == 28


def test_lint_newbie_equipment_thresholds_passes_on_real_data():
    """真实 gamedata 全量过 lint：新手装门槛均 ≤15。"""
    items = {
        key: cast(ItemDef, value)
        for key, value in load_all(GAMEDATA_ROOT)["items"].items()
    }
    problems = lint_newbie_equipment_thresholds(items)
    assert problems == [], f"lint 意外报错: {problems}"


def test_lint_newbie_equipment_thresholds_flags_overcap():
    """QA failure 场景：布衣 need_level=99 → 报错并指出文件/字段。"""
    bad = ItemDef.model_validate(
        {**_load_sample("items", "cloth_robe"), "need_level": 99}
    )
    problems = lint_newbie_equipment_thresholds({bad.id: bad})
    assert len(problems) == 1
    message = problems[0]
    assert "items/cloth_robe.json" in message  # 文件路径
    assert "need_level" in message             # 字段名
    assert "99" in message and "15" in message  # 实际值与上限


def test_cli_validate_exit_1_on_newbie_overcap(broken_root: Path):
    """CLI 集成：改坏布衣门槛后 validate --strict 报错退出 1（todo 22 起 lint 归 strict 门控）。"""
    target = broken_root / "items" / "cloth_robe.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["need_level"] = 99
    target.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    result = _run_cli("validate", "--strict", "--root", str(broken_root))
    assert result.returncode == 1
    assert "items/cloth_robe.json" in result.stderr
    assert "need_level" in result.stderr

# ---------- todo 20：技能 32 + NPC/商店 + 任务 ----------


def test_skills_count_is_32():
    """task 20 验收：gamedata/skills 恰好 32 个。"""
    count = len(list((GAMEDATA_ROOT / "skills").glob("*.json")))
    assert count == 32, f"skills/ 共 {count} 个 != 32"


def test_skill_learn_levels_match_readme():
    """task 20 验收：三职业习得等级与 README §4 表逐项一致（脚本断言）。"""
    from scripts.check_skill_learn_levels import check as check_learn_levels

    problems = check_learn_levels(BACKEND_ROOT)
    assert problems == [], f"习得等级断言失败: {problems}"


def test_magic_shield_learn_level_31():
    """QA 锚点：魔力护盾（旧名魔法盾） L31（README §4.2 表，换皮映射见 docs/reskin-mapping.md）。"""
    s = SkillDef.model_validate(_load_sample("skills", "magic_shield"))
    assert s.name == "魔力护盾"
    assert s.learn_level == 31


def test_level35_trio():
    """QA 锚点：焚焰剑法/冰霜咆哮/召唤灵兽（旧名烈火剑法/冰咆哮/召唤神兽） 均 L35。"""
    for stem, name in (
        ("liehuo", "焚焰剑法"),
        ("ice_thunder", "冰霜咆哮"),
        ("summon_beast", "召唤灵兽"),
    ):
        s = SkillDef.model_validate(_load_sample("skills", stem))
        assert s.learn_level == 35, f"{stem} learn_level={s.learn_level} != 35"
        assert s.name == name


def test_npcs_full_dataset_with_shops():
    """task 20 验收：NPC ≥5，vendor 商店非空且含 §8 售卖明细。"""
    paths = sorted((GAMEDATA_ROOT / "npcs").glob("*.json"))
    assert len(paths) >= 5
    vendor_count = 0
    for path in paths:
        npc = NpcDef.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if npc.kind.value == "vendor":
            vendor_count += 1
            assert npc.shop_items, f"{path.name}: vendor 商店为空"
            entry_ids = {e.item_id for e in npc.shop_entries}
            assert entry_ids == set(npc.shop_items), f"{path.name}: entries/items 不同集"
    assert vendor_count >= 4


def test_quests_full_dataset_and_fate_blade():
    """task 20 验收：任务 ≥8；命运之刃含弱化分身标记。"""
    paths = sorted((GAMEDATA_ROOT / "quests").glob("*.json"))
    assert len(paths) >= 8
    quest = QuestDef.model_validate(_load_sample("quests", "warrior_fate_blade"))
    assert quest.level_req == 35
    assert quest.required_class is not None and quest.required_class.value == "warrior"
    assert quest.target_id == "wild_boar_white"
    assert quest.boss_instance is not None
    assert quest.boss_instance.zone_id == "shimen-5"
    assert quest.boss_instance.weak_clone is True
    assert "fate_blade" in quest.reward_items


def test_quests_no_daily_or_chain_fields():
    """计划红线：任务不加日常/链式字段。"""
    forbidden = {"daily", "repeat", "repeatable", "pre", "chain", "next_quest"}
    for path in (GAMEDATA_ROOT / "quests").glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert not (forbidden & set(raw)), f"{path.name}: 出现禁用字段"


def test_check_skill_learn_levels_flags_drift(tmp_path: Path):
    """QA failure 场景：魔法盾改 30 → 断言报出文件+两边数值。"""
    import shutil

    root = tmp_path / "backend"
    (root / "gamedata").mkdir(parents=True)
    shutil.copytree(GAMEDATA_ROOT / "skills", root / "gamedata" / "skills")
    target = root / "gamedata" / "skills" / "magic_shield.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["learn_level"] = 30
    target.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    from scripts.check_skill_learn_levels import check as check_learn_levels

    problems = check_learn_levels(root)
    assert any(
        "magic_shield.json" in p and "30" in p and "31" in p for p in problems
    ), problems


# ---------- todo 21：掉落表 + 刷怪表 + 全量地图 ----------


_BOSS_REQUIRED: dict[str, set[str]] = {
    "wild_boar_white": {
        "verdict_staff", "bone_jade_staff", "dragonvein_sword",
        "book_fire_sword", "book_ice_roar", "book_summon_beast",
    },
    "wooma_taurus": {"wooma_horn", "verdict_staff", "bone_jade_staff"},
    "zuma_taurus": {
        "bone_jade_staff", "dragonvein_sword", "soul_eater_staff",
        "book_fire_sword", "book_ice_roar", "book_summon_beast",
    },
    "corpse_king": {
        "book_fire_sword", "book_ice_roar", "book_summon_beast",
    },
}
_EGG_ROWS = {
    ("wild_boar_white", "dragonslayer_blade"),
    ("wild_boar_white", "soul_eater_staff"),
    ("touch_dragon_god", "dragonslayer_blade"),
    ("touch_dragon_god", "soul_eater_staff"),
    ("zuma_taurus", "dragonslayer_blade"),
    ("zuma_guardian_elite", "dragonslayer_blade"),
}
_EXPECTED_BANDS: dict[str, tuple[int, int]] = {
    "yin-xing-cun": (0, 7),
    "biqi-wild": (5, 15),
    "shouren-gu-1": (12, 20),
    "shouren-gu-2": (16, 24),
    "shouren-gu-3": (20, 28),
    "biqi-mine": (12, 20),
    "snake-valley": (15, 22),
    "mengzhong": (18, 26),
    "centipede-cave": (20, 30),
    "shimen": (24, 34),
    "wooma-forest": (25, 30),
    "wooma-temple": (28, 36),
    "zuma-caves": (32, 40),
}


def _raw(category: str) -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((GAMEDATA_ROOT / category).glob("*.json"))
    ]


def test_drops_full_dataset_count():
    """task 21 验收：掉落表 ≥30（实际全量 60，一怪一表）。"""
    count = len(list((GAMEDATA_ROOT / "drops").glob("*.json")))
    assert count >= 30, f"drops/ 仅 {count} 表 < 30"
    monsters = {m["id"] for m in _raw("monsters")}
    covered = {d["monster_id"] for d in _raw("drops")}
    assert monsters <= covered, f"缺掉落表: {sorted(monsters - covered)}"


def test_every_drop_table_has_gold_row():
    """验收：每只怪至少有金币掉落行（item_id=null 且 gold_max>0）。"""
    no_gold = [
        d["monster_id"]
        for d in _raw("drops")
        if not any(
            e["item_id"] is None and e["gold_max"] > 0 for e in d["entries"]
        )
    ]
    assert no_gold == [], f"无金币行的掉落表: {no_gold}"


def test_boss_named_drops_present():
    """验收：BOSS 掉落表含文档点名物品。"""
    tables = {d["monster_id"]: d for d in _raw("drops")}
    for mid, required in _BOSS_REQUIRED.items():
        have = {e["item_id"] for e in tables[mid]["entries"] if e["item_id"]}
        lack = required - have
        assert not lack, f"{mid} 缺点名物品: {sorted(lack)}"


def test_egg_items_extremely_rare():
    """验收：屠龙/嗜魂彩蛋 weight=1 且出现率 ≤0.001。"""
    tables = {d["monster_id"]: d for d in _raw("drops")}
    found = 0
    for mid, table in tables.items():
        total = sum(e["weight"] for e in table["entries"])
        for e in table["entries"]:
            if e["item_id"] is None:
                continue
            if (mid, e["item_id"]) in _EGG_ROWS:
                found += 1
                assert e["weight"] == 1, f"{mid}.{e['item_id']} weight != 1"
                assert e["weight"] / total <= 0.001, (
                    f"{mid}.{e['item_id']} 出现率 > 0.001"
                )
    assert found >= len(_EGG_ROWS), f"彩蛋行缺失: {found}/{len(_EGG_ROWS)}"


def test_spawns_full_dataset_coverage():
    """验收：刷怪点 ≥30、每图 ≥1 点、respawn 与 monsters 单一来源一致。"""
    spawns = _raw("spawns")
    assert len(spawns) >= 30, f"spawns/ 仅 {len(spawns)} 点 < 30"
    respawn = {m["id"]: m["respawn_seconds"] for m in _raw("monsters")}
    maps_with_spawns = {s["map_id"] for s in spawns}
    all_maps = {m["id"] for m in _raw("maps")}
    assert all_maps <= maps_with_spawns, f"无刷怪点的地图: {all_maps - maps_with_spawns}"
    for s in spawns:
        assert s["respawn_seconds"] == respawn[s["monster_id"]], s
        pairs = [(loc["x"], loc["y"]) for loc in s["locations"]]
        assert len(pairs) == len(set(pairs)), f"{s} 点位重复"


def test_maps_full_dataset_with_fields():
    """验收：maps ≥10；等级带基线 + 邻接双向对称 + BOSS 落位有刷怪点。"""
    maps = {m["id"]: m for m in _raw("maps")}
    assert len(maps) >= 10, f"maps/ 仅 {len(maps)} 张 < 10"
    for mid, m in maps.items():
        assert m["name"] and m["region"], mid
        expected = _EXPECTED_BANDS.get(mid)
        assert expected is not None, f"{mid} 不在等级带基线表内"
        assert (m["level_min"], m["level_max"]) == expected, mid
        for other in m["adjacent"]:
            assert other != mid, f"{mid} 自环"
            assert mid in maps[other]["adjacent"], f"{mid}->{other} 单向邻接"
    spawn_keys = {f"{s['map_id']}:{s['monster_id']}" for s in _raw("spawns")}
    for mid, m in maps.items():
        for boss_id in m["boss_spawns"]:
            assert f"{mid}:{boss_id}" in spawn_keys, f"{mid} 缺 BOSS 刷怪点 {boss_id}"


def test_travel_seconds_constants():
    """balance 新增行进中时长参数：5–20s（README §10 区间端点）。"""
    from app.combat import balance

    assert balance.TRAVEL_SECONDS_MIN == 5
    assert balance.TRAVEL_SECONDS_MAX == 20
    assert balance.TRAVEL_SECONDS_MIN < balance.TRAVEL_SECONDS_MAX


def test_simulate_drops_distribution_in_tolerance(capsys: Any):
    """验收 happy path：1000 次白野猪击杀分布报告在容差内（exit 0）。"""
    from scripts.simulate_drops import main as simulate_main

    assert simulate_main([]) == 0
    out = capsys.readouterr().out
    assert "沃玛装备出现率" in out
    assert "容差断言全部通过" in out


# ---------- todo 22：全量校验门禁（strict lint 规则） ----------


def _loaded() -> dict[str, dict[str, Any]]:
    """加载真实 gamedata 索引（cast 放宽 GameDataModel 边界，便于合成污染）。"""
    return cast("dict[str, dict[str, Any]]", load_all(GAMEDATA_ROOT))


def test_lint_cross_refs_integrity_passes_on_real_data():
    """规则 1 happy path：真实数据零悬空引用。"""
    problems = lint_cross_refs_integrity(_loaded())
    assert problems == [], problems


def test_lint_cross_refs_integrity_flags_dangling_drop_item():
    """规则 1 failure：drops 引用不存在的 item → 报文件+字段+期望类别。"""
    data = _loaded()
    raw = _load_sample("drops", "chicken_drops")
    raw["entries"][1]["item_id"] = "nonexistent_item"
    bad = DropEntry.model_validate(raw)
    tainted = {**data, "drops": {**data["drops"], "ghost_table": bad}}
    problems = lint_cross_refs_integrity(tainted)
    assert len(problems) == 1, problems
    assert "entries[1].item_id" in problems[0]
    assert "'nonexistent_item' not found in items" in problems[0]


def test_lint_birth_map_exists_passes_on_real_data():
    """规则 2 happy path：BIRTH_MAP_ID='yin-xing-cun' 在 maps。"""
    maps = cast("dict[str, MapDef]", _loaded()["maps"])
    assert lint_birth_map_exists(maps) == []


def test_lint_birth_map_exists_flags_missing():
    """规则 2 failure：删掉出生图 → 报 BIRTH_MAP_ID 未找到。"""
    maps = cast("dict[str, MapDef]", _loaded()["maps"])
    problems = lint_birth_map_exists({k: v for k, v in maps.items() if k != "yin-xing-cun"})
    assert len(problems) == 1
    assert "BIRTH_MAP_ID 'yin-xing-cun' not found in maps" in problems[0]


def test_lint_monster_level_in_band_passes_on_real_data():
    """规则 3 happy path：BOSS 豁免 + 登记例外覆盖后，真实数据全过。"""
    data = _loaded()
    problems = lint_monster_level_in_band(
        cast("dict[str, SpawnEntry]", data["spawns"]),
        cast("dict[str, MonsterDef]", data["monsters"]),
        cast("dict[str, MapDef]", data["maps"]),
    )
    assert problems == [], problems


def test_lint_monster_level_in_band_flags_misplaced_nonboss():
    """规则 3 failure：未登记的越带普通怪 → 报怪/图/实际等级/期望范围。"""
    data = _loaded()
    monsters = cast("dict[str, MonsterDef]", dict(data["monsters"]))
    misplaced = MonsterDef.model_validate(
        {**_load_sample("monsters", "chicken"), "id": "misplaced_ogre", "level": 99}
    )
    monsters["misplaced_ogre"] = misplaced
    spawns = cast("dict[str, SpawnEntry]", dict(data["spawns"]))
    spawn = SpawnEntry.model_validate(
        {**_load_sample("spawns", "yin-xing-cun_chicken"), "monster_id": "misplaced_ogre"}
    )
    spawns["yin-xing-cun:misplaced_ogre"] = spawn
    problems = lint_monster_level_in_band(
        spawns,
        monsters,
        cast("dict[str, MapDef]", data["maps"]),
    )
    assert len(problems) == 1, problems
    assert "misplaced_ogre" in problems[0]
    assert "yin-xing-cun" in problems[0]
    assert "level 99" in problems[0]
    assert "[-3, 10]" in problems[0]  # 银杏村带 0-7 ±3


def test_lint_monster_level_in_band_exempts_boss():
    """规则 3 豁免：boss=True 越带不报（经典低级图藏高级 BOSS）。"""
    data = _loaded()
    monsters = cast("dict[str, MonsterDef]", dict(data["monsters"]))
    boss = MonsterDef.model_validate(
        {**_load_sample("monsters", "chicken"), "id": "hidden_boss", "level": 99, "boss": True}
    )
    monsters["hidden_boss"] = boss
    spawns = cast("dict[str, SpawnEntry]", dict(data["spawns"]))
    spawn = SpawnEntry.model_validate(
        {**_load_sample("spawns", "yin-xing-cun_chicken"), "monster_id": "hidden_boss"}
    )
    spawns["yin-xing-cun:hidden_boss"] = spawn
    problems = lint_monster_level_in_band(
        spawns,
        monsters,
        cast("dict[str, MapDef]", data["maps"]),
    )
    assert problems == [], problems


def test_lint_weapon_level_sequence_passes_on_real_data():
    """规则 4 happy path：11 个 §7 基线锚点全部对齐。"""
    items = cast("dict[str, ItemDef]", _loaded()["items"])
    problems = lint_weapon_level_sequence(items)
    assert problems == [], problems


def test_lint_weapon_level_sequence_flags_drift():
    """规则 4 failure：裁决之杖 need_level 漂移 → 报实际值 vs 基线值。"""
    bad = ItemDef.model_validate(
        {**_load_sample("items", "verdict_staff"), "need_level": 31}
    )
    problems = lint_weapon_level_sequence({bad.id: bad})
    assert len(problems) == 1
    assert "items/verdict_staff.json" in problems[0]
    assert "31" in problems[0] and "30" in problems[0]


def test_lint_weapon_level_sequence_tolerates_extra_and_missing():
    """规则 4 边界：基线外新武器与缺失锚点都不硬错——diff 报告而非严格匹配。"""
    extra = ItemDef.model_validate(_load_sample("items", "dragonslayer_blade"))
    assert lint_weapon_level_sequence({extra.id: extra}) == []
    report = weapon_level_sequence_report({extra.id: extra})
    assert any("[EXTRA]" in line for line in report)
    assert any("[MISSING]" in line for line in report)


def test_lint_id_only_references_passes_on_real_data():
    """规则 5 happy path：真实 gamedata 除自由文本字段外无中文。"""
    assert lint_id_only_references(GAMEDATA_ROOT) == []


def test_lint_id_only_references_flags_cjk_reference(broken_root: Path):
    """规则 5 failure：spawn 用中文名引用地图 → 报文件+字段路径。"""
    target = broken_root / "spawns" / "yin-xing-cun_chicken.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["map_id"] = "银杏村"
    target.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    problems = lint_id_only_references(broken_root)
    assert problems, "中文引用未被拦截"
    assert any(
        "yin-xing-cun_chicken.json" in p and "map_id" in p and "银杏村" in p
        for p in problems
    ), problems


def test_cli_validate_strict_exit_0():
    """验收：validate --strict 全绿 exit 0。"""
    result = _run_cli("validate", "--strict")
    assert result.returncode == 0, result.stderr
    assert "(strict)" in result.stdout


def test_cli_validate_no_strict_skips_semantic_lint(broken_root: Path):
    """门控边界：非 strict 只跑结构校验，语义 lint 不拦截。"""
    target = broken_root / "items" / "cloth_robe.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["need_level"] = 99
    target.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    result = _run_cli("validate", "--no-strict", "--root", str(broken_root))
    assert result.returncode == 0, result.stderr


def test_cli_validate_strict_exit_1_on_dangling_drop_item(broken_root: Path):
    """验收 failure：故意制造悬空引用 → strict exit 1 + 报错路径。"""
    target = broken_root / "drops" / "chicken_drops.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["entries"][1]["item_id"] = "nonexistent_item"
    target.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    result = _run_cli("validate", "--strict", "--root", str(broken_root))
    assert result.returncode == 1
    assert "nonexistent_item" in result.stderr
    assert "drops/chicken_drops.json" in result.stderr


def test_cli_validate_strict_catches_drift_structural_misses(broken_root: Path):
    """门控价值实证：need_level 漂移通过结构校验、仅 strict lint 拦截。"""
    target = broken_root / "items" / "verdict_staff.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["need_level"] = 31
    target.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    structural = _run_cli("validate", "--no-strict", "--root", str(broken_root))
    assert structural.returncode == 0, structural.stderr
    strict = _run_cli("validate", "--strict", "--root", str(broken_root))
    assert strict.returncode == 1
    assert "warrior baseline drift" in strict.stderr
    assert "items/verdict_staff.json" in strict.stderr
