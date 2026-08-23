"""balance.py 防回归测试：系数 sanity、出处纪律、文档覆盖、类型与交叉校验。

对应 docs/combat-formulas.md（todo 8）；grep 防回归 = 计划验收标准 #2。
"""

import re
from pathlib import Path
from typing import Final

from app.combat import balance
from app.core.config import settings

BACKEND_DIR: Final[Path] = Path(__file__).resolve().parent.parent
COMBAT_DIR: Final[Path] = BACKEND_DIR / "app" / "combat"
DOC_PATH: Final[Path] = BACKEND_DIR.parent / "docs" / "combat-formulas.md"
DECIMAL_RE: Final[re.Pattern[str]] = re.compile(r"0\.[0-9]")


# --- 1. 系数 sanity：关键系数在合理区间 ---------------------------------------


def test_hit_rate_clamp_bounds() -> None:
    """命中率截断必须夹在 (0,1) 开区间内且为社区惯例的 5%/95%。"""
    assert 0 < balance.MIN_HIT_RATE < balance.MAX_HIT_RATE < 1
    assert balance.MIN_HIT_RATE == 0.05
    assert balance.MAX_HIT_RATE == 0.95


def test_coefficient_sanity_ranges() -> None:
    """其余系数的物理合理区间（防手滑改出负防御/超 100% 阈值之类）。"""
    assert 0 <= balance.LUCK_HIT_BIAS <= 0.5
    assert 0 <= balance.CURSE_HIT_BIAS <= 0.5
    assert 0 < balance.DEF_DAMAGE_REDUCTION_FACTOR <= 0.2
    assert balance.SKILL_MULTIPLIER_BASE == 1.0
    assert 0 < balance.POTION_HP_PCT < 1
    assert 0 < balance.POTION_MP_PCT < 1
    assert balance.GLOBAL_DROP_RATE > 0
    assert balance.MIN_DAMAGE >= 1
    assert balance.ATTACK_INTERVAL_TICKS >= 1
    assert balance.LUCK_MAX >= 1


# --- 2. 类型正确：全部模块级 Final 且标量类型精确 -------------------------------


def test_coefficient_types_are_final_and_exact() -> None:
    """每个系数都是 Final 注解 + 精确标量类型（bool 是 int 子类，用 type 排除）。"""
    float_names = (
        "MIN_HIT_RATE",
        "MAX_HIT_RATE",
        "LUCK_HIT_BIAS",
        "CURSE_HIT_BIAS",
        "DEF_DAMAGE_REDUCTION_FACTOR",
        "SKILL_MULTIPLIER_BASE",
        "POTION_HP_PCT",
        "POTION_MP_PCT",
        "GLOBAL_DROP_RATE",
    )
    int_names = (
        "LUCK_MAX",
        "MIN_DAMAGE",
        "ATTACK_INTERVAL_TICKS",
        "GRACE_PERIOD_SECONDS",
        "BAG_CAPACITY",
    )
    values: dict[str, float | int] = {
        "MIN_HIT_RATE": balance.MIN_HIT_RATE,
        "MAX_HIT_RATE": balance.MAX_HIT_RATE,
        "LUCK_HIT_BIAS": balance.LUCK_HIT_BIAS,
        "CURSE_HIT_BIAS": balance.CURSE_HIT_BIAS,
        "DEF_DAMAGE_REDUCTION_FACTOR": balance.DEF_DAMAGE_REDUCTION_FACTOR,
        "SKILL_MULTIPLIER_BASE": balance.SKILL_MULTIPLIER_BASE,
        "POTION_HP_PCT": balance.POTION_HP_PCT,
        "POTION_MP_PCT": balance.POTION_MP_PCT,
        "GLOBAL_DROP_RATE": balance.GLOBAL_DROP_RATE,
        "LUCK_MAX": balance.LUCK_MAX,
        "MIN_DAMAGE": balance.MIN_DAMAGE,
        "ATTACK_INTERVAL_TICKS": balance.ATTACK_INTERVAL_TICKS,
        "GRACE_PERIOD_SECONDS": balance.GRACE_PERIOD_SECONDS,
        "BAG_CAPACITY": balance.BAG_CAPACITY,
    }
    source = (COMBAT_DIR / "balance.py").read_text(encoding="utf-8")
    assert set(values) == {*float_names, *int_names}
    for name, value in values.items():
        expected = float if name in float_names else int
        pattern = rf"^{re.escape(name)}:\s*Final\[{expected.__name__}\]"
        assert re.search(pattern, source, re.MULTILINE), (
            f"{name} 必须声明为 Final[{expected.__name__}]"
        )
        assert type(value) is expected, name


# --- 3. 出处纪律：每个系数带 出处/置信度 注释 -----------------------------------


def test_every_constant_has_provenance_comment() -> None:
    """__all__ 里每个名字的定义行 ±2 行内必须有 '出处:' 与 '置信度:'。"""
    source_lines = (COMBAT_DIR / "balance.py").read_text(encoding="utf-8").splitlines()
    for name in balance.__all__:
        def_indices = [
            i for i, line in enumerate(source_lines) if name in line
        ]
        assert def_indices, f"{name} 未在 balance.py 中找到"
        ok = False
        confidence_values: list[str] = []
        for i in def_indices:
            window = "\n".join(source_lines[max(0, i - 2) : i + 3])
            if "出处:" in window and "置信度:" in window:
                ok = True
                confidence_values = re.findall(r"置信度:\s*(\w+)", window)
        assert ok, f"{name} 缺少 出处:/置信度: 注释"
        assert set(confidence_values) <= {"high", "medium", "low"}, name


def test_no_decimal_literals_outside_balance() -> None:
    """combat/ 下除 balance.py 外禁止 0.x 字面量（系数唯一来源防回归）。

    同时反向断言 balance.py 自身确实含小数字面量——防止本检查空转通过。
    """
    violations: list[str] = []
    for path in sorted(COMBAT_DIR.rglob("*.py")):
        if path.name == "balance.py":
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if DECIMAL_RE.search(line):
                violations.append(f"{path.relative_to(BACKEND_DIR)}:{lineno}")
    assert not violations, f"系数散落在 balance.py 之外: {violations}"
    balance_source = (COMBAT_DIR / "balance.py").read_text(encoding="utf-8")
    assert DECIMAL_RE.search(balance_source), "balance.py 应含小数系数（防空转）"


# --- 4. 交叉校验：双写常量与 models/config 一致 ---------------------------------


def test_cross_check_models_and_config() -> None:
    """BAG_CAPACITY ↔ models 列默认值；GRACE_PERIOD_SECONDS ↔ config SSE 设置。"""
    character_src = (BACKEND_DIR / "app" / "models" / "character.py").read_text(
        encoding="utf-8"
    )
    assert f"default={balance.BAG_CAPACITY}" in character_src
    assert f'server_default="{balance.BAG_CAPACITY}"' in character_src
    assert balance.GRACE_PERIOD_SECONDS == int(settings.SSE_GRACE_PERIOD_SECONDS)
    assert balance.BIRTH_MAP_ID == "yin-xing-cun"


# --- 5. 文档存在性 + 5 个公式节覆盖 --------------------------------------------


def _doc_sections() -> dict[str, str]:
    """按 '## ' 标题切分 combat-formulas.md，返回 {标题: 节正文}。"""
    text = DOC_PATH.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current_title = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current_title = line[3:].strip()
            sections[current_title] = ""
        elif current_title:
            sections[current_title] += line + "\n"
    return sections


def test_doc_covers_five_formula_sections() -> None:
    """命中/伤害/减伤/幸运诅咒/经验曲线 5 节齐全，各带出处+置信度+手算推导。"""
    assert DOC_PATH.exists(), f"缺少 {DOC_PATH}"
    sections = _doc_sections()
    required_keys = {
        "命中": "命中公式",
        "伤害": "伤害公式",
        "减伤": "减伤",
        "幸运": "幸运与诅咒",
        "经验": "经验曲线",
    }
    matched_titles: dict[str, str] = {}
    for key, label in required_keys.items():
        hits = [title for title in sections if key in title]
        assert hits, f"文档缺少「{label}」节"
        matched_titles[key] = hits[0]
    for key, title in matched_titles.items():
        body = sections[title]
        assert "出处" in body, f"「{title}」节缺出处"
        assert "置信度" in body, f"「{title}」节缺置信度"
        assert "手算" in body, f"「{title}」节缺手算推导"
    full_text = DOC_PATH.read_text(encoding="utf-8")
    assert "thelegendofmir.com" in full_text, "文档应引用公开社区源"
    assert "balance.py" in full_text, "文档应指向系数唯一来源"
