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
from typing import Any

import pytest

from app.gamedata_loader import GameDataError, load_all
from app.gamedata_schema import (
    DropEntry,
    ItemDef,
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
    return json.loads((GAMEDATA_ROOT / category / f"{stem}.json").read_text(encoding="utf-8"))


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
    assert d.entries[0].item_id is None  # 金币掉落
    assert d.entries[1].item_id == "wooden_sword"


def test_spawn_sample_ok():
    s = SpawnEntry.model_validate(_load_sample("spawns", "yin-xing-cun_chicken"))
    assert s.map_id == "yin-xing-cun"
    assert s.locations[0].x == 100


def test_load_all_ok():
    data = load_all(GAMEDATA_ROOT)
    assert set(data) == {
        "maps", "monsters", "items", "skills", "npcs", "quests", "drops", "spawns",
    }
    assert "yin-xing-cun" in data["maps"]
    assert "chicken" in data["drops"]
    assert "yin-xing-cun:chicken" in data["spawns"]


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
