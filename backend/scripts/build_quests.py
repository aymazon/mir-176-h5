"""build_quests.py — task 20 任务数据录入构建器。

数据来源与策略（docs/data-coverage.md §7 同步记录）：
- 任务清单按 README §9：新手引导 4 / 各区悬赏 3 / 收集 3 / 35 级职业专属 3。
- 奖励数值自定（低收益点缀，M5 校准）；素材/奖励物品 id 均为 items/ 既有 id。
- 命运之刃（战士 35 级）：kind=kill 目标白野猪，boss_instance 标注
  zone_id="shimen-5"（石墓 5 层分区，todo 21 落地）+ weak_clone=true（弱化分身）。
- 红线遵守：无日常/链式字段（无 pre/daily/repeat）。

用法：cd backend && uv run python scripts/build_quests.py [--dry-run]
"""

# pyright: reportUnusedCallResult=false, reportImplicitRelativeImport=false

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gamedata_schema.quests import QuestDef

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_QUESTS_DIR = _BACKEND_ROOT / "gamedata" / "quests"


class QuestValues(TypedDict):
    id: str
    name: str
    kind: str
    level_req: int
    required_class: str | None
    target_id: str
    target_count: int
    reward_gold: int
    reward_exp: int
    reward_items: list[str]
    dialogue: dict[str, str]
    boss_instance: dict[str, Any] | None


def _q(
    qid: str,
    name: str,
    kind: str,
    level_req: int,
    target_id: str,
    target_count: int,
    reward_gold: int,
    reward_exp: int,
    accept: str,
    complete: str,
    *,
    required_class: str | None = None,
    reward_items: list[str] | None = None,
    boss_instance: dict[str, Any] | None = None,
) -> QuestValues:
    return {
        "id": qid,
        "name": name,
        "kind": kind,
        "level_req": level_req,
        "required_class": required_class,
        "target_id": target_id,
        "target_count": target_count,
        "reward_gold": reward_gold,
        "reward_exp": reward_exp,
        "reward_items": reward_items or [],
        "dialogue": {
            "accept": accept,
            "progress": f"{name}还没完成，继续加油。",
            "complete": complete,
        },
        "boss_instance": boss_instance,
    }


def roster() -> list[QuestValues]:
    return [
        # ---- 新手引导（银杏村/比奇，1-10 级，共 4 个）----
        _q(
            "newbie_meet_chief", "拜会村长", "dialogue", 1, "yinxing_chief", 1,
            50, 30,
            "初来乍到，先去见见村长吧。",
            "村长对你很满意，收下这点盘缠吧。",
        ),
        _q(
            "newbie_kill_5", "除害新手村", "kill", 1, "chicken", 5,
            100, 50, "村外的鸡太多了，帮我杀 5 只。", "干得漂亮，这是你的奖励。",
            reward_items=["wooden_sword"],
        ),
        _q(
            "newbie_butcher_meat", "屠夫的肉", "collect", 2, "meat", 10,
            80, 60,
            "给我带 10 块新鲜的肉来，杀鸡割鹿都行。",
            "嗯，肉很新鲜，这些药水你拿着。",
            reward_items=["potion_hp_small"],
        ),
        _q(
            "newbie_letter_bookstore", "送信比奇", "dialogue", 5, "biqi_bookstore", 1,
            100, 120,
            "把这封信送到比奇书店老板手上。",
            "信我收到了，替我谢谢村长。",
        ),
        # ---- 击杀悬赏（各主城 NPC，10-35 级，一次性）----
        _q(
            "bounty_skeleton_20", "清剿骷髅洞", "kill", 10, "skeleton", 20,
            500, 1500,
            "骷髅洞的骷髅越来越猖狂，去清剿 20 只。",
            "干得好，比奇欠你一份人情。",
        ),
        _q(
            "bounty_clamp_worm_15", "土城悬赏：钳虫", "kill", 20, "evil_clamp_worm", 15,
            1200, 6000,
            "死亡山谷的邪恶钳虫赏金很高，取 15 只性命来。",
            "赏金如约奉上，好样的。",
        ),
        _q(
            "bounty_orc_champion_10", "讨伐半兽勇士", "kill", 25, "orc_champion", 10,
            2000, 12000,
            "沃玛森林的半兽勇士威胁商路，讨伐 10 只。",
            "商路暂时安全了，这是你的报酬。",
        ),
        # ---- 收集类（可重复但低收益——不加 repeat 字段，红线）----
        _q(
            "collect_maggot_egg", "蛆卵收购", "collect", 12, "maggot_egg", 5,
            150, 400,
            "药店要 5 枚蛆卵入药，价格公道。",
            "蛆卵完好，钱拿好。",
        ),
        _q(
            "collect_spider_fang", "蜘蛛牙收购", "collect", 16, "spider_fang", 5,
            200, 700,
            "铁匠想拿 5 颗蜘蛛牙做毒刃。",
            "成色不错，下次再来。",
        ),
        _q(
            "collect_flower_fruit", "食人花果", "collect", 4, "maneating_flower_fruit", 5,
            100, 150,
            "食人花的果子是难得的药材，带 5 颗回来。",
            "果子很新鲜，辛苦了。",
        ),
        # ---- 35 级职业专属 ----
        _q(
            "warrior_fate_blade", "命运之刃", "kill", 35, "wild_boar_white", 1,
            0, 20000,
            "收集玄铁交给铁匠铸出剑坯，再前往石墓五层挑战白野猪的弱化分身，"
            "以战意淬火方可开锋。",
            "剑已成——命运之刃，配得上真正的战士！",
            required_class="warrior",
            reward_items=["fate_blade"],
            boss_instance={"zone_id": "shimen-5", "weak_clone": True},
        ),
        _q(
            "wizard_ice_roar_guide", "冰咆哮书引", "profession", 35, "ice_thunder", 1,
            500, 10000,
            "冰咆哮的秘密藏在石墓深处——白野猪守护着技能书，去把它夺回来。",
            "恭喜你掌握了大范围群攻的奥义。",
            required_class="wizard",
            reward_items=["book_ice_roar"],
        ),
        _q(
            "taoist_summon_beast_guide", "神兽召唤引", "profession", 35, "summon_beast", 1,
            500, 10000,
            "沃玛教主手中有一册召唤神兽的书，取回它，让神兽听你号令。",
            "从此神兽与你同行。",
            required_class="taoist",
            reward_items=["book_summon_beast"],
        ),
    ]


def build(dry_run: bool = False) -> int:
    quests = roster()
    written = 0
    for values in quests:
        quest = QuestDef.model_validate(values)
        payload = json.dumps(values, ensure_ascii=False, indent=2) + "\n"
        if dry_run:
            print(f"[dry-run] quests/{quest.id}.json")
            continue
        _QUESTS_DIR.mkdir(parents=True, exist_ok=True)
        (_QUESTS_DIR / f"{quest.id}.json").write_text(payload, encoding="utf-8")
        written += 1
    print(f"[build] quests/ 共写入 {written} 个（新手 4 / 悬赏 3 / 收集 3 / 职业专属 3）")
    return 0


if __name__ == "__main__":
    sys.exit(build("--dry-run" in sys.argv))
