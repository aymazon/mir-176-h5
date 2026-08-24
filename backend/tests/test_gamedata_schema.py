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

from app.gamedata_lint import lint_newbie_equipment_thresholds
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
    assert n.shop_items == ["wooden_sword"]


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
        "levels",
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
    """待定项裁决：无极棍按社区主流 28 级录入（置信度中）。"""
    item = ItemDef.model_validate(_load_sample("items", "wuji_cudgel"))
    assert item.name == "无极棍"
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
    """CLI 集成：改坏布衣门槛后 validate --root 报错退出 1。"""
    target = broken_root / "items" / "cloth_robe.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["need_level"] = 99
    target.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    result = _run_cli("validate", "--root", str(broken_root))
    assert result.returncode == 1
    assert "items/cloth_robe.json" in result.stderr
    assert "need_level" in result.stderr
