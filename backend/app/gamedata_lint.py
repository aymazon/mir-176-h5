"""Gamedata lint 规则（todo 19 起步；todo 22 接入 --strict 门控）。

规则与 gamedata_loader 的结构校验解耦：loader 管「形状/引用合法性」，
lint 管「数值语义合理性」。每条规则是纯函数，输入已加载数据索引，
输出问题清单（文件相对路径 + 字段 + 原因），供 gamedata_cli validate
直接调用，也可在单测中独立调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gamedata_schema.items import ItemDef

# 新手装词缀 → 门槛上限。命中词缀的 weapon/armor 必须 need_level ≤ 上限，
# 否则视为录入错误（如布衣误录 need_level=99）。
NEWBIE_EQUIPMENT_RULES: tuple[tuple[str, int], ...] = (
    ("布衣", 15),
    ("木剑", 15),  # 含乌木剑
    ("铁剑", 15),
    ("短剑", 15),
    ("匕首", 15),
    ("青铜剑", 15),
    ("轻型盔甲", 15),
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
