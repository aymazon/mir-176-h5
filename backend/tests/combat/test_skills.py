"""combat/skills.py 单测（todo 11，TDD 表驱动）。

数据来源纪律：
- 期望值一律从 fixture（tests/fixtures/skills_32.json）或真实 gamedata 推导，
  测试内不硬编码技能数值（数值唯一来源 = gamedata / fixture）；
- 唯一允许的字面量锚点是 docs/MVP/README.md §4 的「习得等级」列（外部契约）。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.combat.skills import (
    CastAttempt,
    SkillNotFoundError,
    SkillRegistry,
    SkillState,
    grant_cast_exp,
    load_registry,
    try_cast,
)

FIXTURES_DIR: Path = Path(__file__).resolve().parent.parent / "fixtures"
FIXTURE_PATH: Path = FIXTURES_DIR / "skills_32.json"

# README §4 外部契约锚点：习得等级（唯一硬编码进测试的数值，用于防手抄错行）。
README_LEARN_LEVELS: dict[str, int] = {
    # 战 6（§4.1）
    "basic_sword": 7,
    "tae_kwon_do": 19,
    "thrust": 25,
    "half_moon": 28,
    "soul_shoulder": 30,
    "liehuo": 35,
    # 法 14（§4.2）
    "fireball": 7,
    "repulsion": 12,
    "temptation": 13,
    "hell_fire": 16,
    "thunder_bolt": 17,
    "great_fireball": 19,
    "teleport": 19,
    "blast_fire": 22,
    "fire_wall": 24,
    "lightning": 26,
    "lightning_exorcism": 30,
    "magic_shield": 31,
    "holy_word": 32,
    "ice_thunder": 35,
    # 道 12（§4.3）
    "healing": 7,
    "spirit_sword": 9,
    "poison_amulet": 14,
    "soul_fire": 18,
    "summon_skeleton": 19,
    "invisibility": 20,
    "mass_invisibility": 21,
    "ghost_shield": 22,
    "holy_armor": 25,
    "trap_curse": 28,
    "mass_heal": 33,
    "summon_beast": 35,
}


def _load_fixture() -> dict[str, dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fixture_data() -> dict[str, dict[str, Any]]:
    return _load_fixture()


@pytest.fixture(scope="module")
def registry(fixture_data: dict[str, dict[str, Any]]) -> SkillRegistry:
    return SkillRegistry(fixture_data)


# --- 1. fixture 完整性：32 技能、职业分布、§4 锚点 ------------------------------


def test_fixture_has_32_skills_with_class_split(
    fixture_data: dict[str, dict[str, Any]],
) -> None:
    """推导：任务规格 战6+法14+道12=32；分布由 fixture 自身统计而非硬编码清单。"""
    assert len(fixture_data) == 32
    by_class: dict[str, int] = {}
    for raw in fixture_data.values():
        by_class[raw["class"]] = by_class.get(raw["class"], 0) + 1
    assert by_class == {"warrior": 6, "wizard": 14, "taoist": 12}


def test_fixture_learn_levels_match_readme_anchor(
    fixture_data: dict[str, dict[str, Any]],
) -> None:
    """推导：fixture 是 §4 表的手抄，习得等级必须与 README 锚点逐一相等。"""
    assert set(fixture_data) == set(README_LEARN_LEVELS)
    for skill_id, learn_level in README_LEARN_LEVELS.items():
        assert fixture_data[skill_id]["learn_level"] == learn_level, skill_id


def test_fixture_entries_are_schema_valid(fixture_data: dict[str, dict[str, Any]]) -> None:
    """推导：注册表经 SkillDef 校验；fixture 必须全量通过（与 gamedata 同 schema）。"""
    SkillRegistry(fixture_data)  # 构造即校验，extra="forbid" 会拦未知字段


# --- 2. 注册表表驱动：32 技能字段走查 -------------------------------------------


def test_registry_exposes_every_fixture_field(
    registry: SkillRegistry, fixture_data: dict[str, dict[str, Any]]
) -> None:
    """推导：注册表是 gamedata 的只读投影，每个字段都应与原始 JSON 相等。"""
    for skill_id, raw in fixture_data.items():
        skill = registry.get(skill_id)
        assert skill.id == raw["id"]
        assert skill.name == raw["name"]
        assert skill.class_.value == raw["class"]
        assert skill.learn_level == raw["learn_level"]
        assert skill.mp_cost == raw["mp_cost"]
        assert skill.cooldown_seconds == raw["cooldown_seconds"]
        assert skill.max_level == raw["max_level"]
        assert skill.level_exp_required == raw["level_exp_required"]
        assert skill.target.value == raw["target"]


@pytest.mark.parametrize("skill_id", sorted(README_LEARN_LEVELS))
def test_can_learn_boundary_per_skill(
    registry: SkillRegistry, skill_id: str
) -> None:
    """推导：成功率条件之一 = 角色等级 ≥ 习得等级；边界差 1 级必须翻转。"""
    learn_level = registry.get(skill_id).learn_level
    assert registry.can_learn(skill_id, learn_level - 1) is False
    assert registry.can_learn(skill_id, learn_level) is True
    assert registry.can_learn(skill_id, learn_level + 10) is True


@pytest.mark.parametrize("skill_id", sorted(README_LEARN_LEVELS))
def test_exp_thresholds_read_from_definition(
    registry: SkillRegistry, skill_id: str
) -> None:
    """推导：跨级阈值 = level_exp_required[level]；满级后无阈值（哨兵 0）。"""
    skill = registry.get(skill_id)
    for level, expected in enumerate(skill.level_exp_required):
        assert registry.read_exp_threshold(skill_id, level) == expected
    assert registry.read_exp_threshold(skill_id, skill.max_level) == 0


def test_ids_by_class_filter(registry: SkillRegistry) -> None:
    """推导：按职业取 id 集合应与 fixture 分组一致（供 sim 自动战斗选技能）。"""
    for class_name, count in (("warrior", 6), ("wizard", 14), ("taoist", 12)):
        ids = registry.ids_by_class(class_name)
        assert len(ids) == count
        assert all(
            registry.get(i).class_.value == class_name for i in ids
        )


# --- 3. 倍率：标量恒定 + 每级覆盖键 ---------------------------------------------


def test_scalar_multiplier_constant_across_levels(
    registry: SkillRegistry, fixture_data: dict[str, dict[str, Any]]
) -> None:
    """推导：effect_params 只有标量 damage_multiplier 时，各等级倍率相同且等于该值。"""
    base = fixture_data["fireball"]["effect_params"]["damage_multiplier"]
    for level in range(3):
        assert registry.get_multiplier("fireball", level) == base


def test_liehuo_level_multipliers_from_override_keys(
    registry: SkillRegistry, fixture_data: dict[str, dict[str, Any]]
) -> None:
    """fail case 实证：烈火剑法 3 级倍率用例绿——期望值从 fixture 覆盖键推导。"""
    params = fixture_data["liehuo"]["effect_params"]
    assert registry.get_multiplier("liehuo", 0) == params["damage_multiplier"]
    assert registry.get_multiplier("liehuo", 1) == params["damage_multiplier_l1"]
    assert registry.get_multiplier("liehuo", 2) == params["damage_multiplier_l2"]
    # 每级递增（README §5.5：技能每级提升倍率）
    assert (
        registry.get_multiplier("liehuo", 0)
        < registry.get_multiplier("liehuo", 1)
        < registry.get_multiplier("liehuo", 2)
    )


def test_unknown_skill_get_and_cast_raise(registry: SkillRegistry) -> None:
    """fail case 实证：未知技能 id 施放 → 报错（SkillNotFoundError 且带 id）。"""
    with pytest.raises(SkillNotFoundError, match="kaitian_zhan"):
        registry.get("kaitian_zhan")
    state = SkillState(skill_id="kaitian_zhan")
    with pytest.raises(SkillNotFoundError, match="kaitian_zhan"):
        try_cast(
            registry,
            "kaitian_zhan",
            char_level=40,
            mp=100,
            state=state,
            last_cast_tick=None,
            now_tick=0,
        )
    # 1.8+ 技能（开天斩等）不在 MVP：既不在 fixture 也不在真实 gamedata
    assert "kaitian_zhan" not in load_registry()


# --- 4. 升级机制：使用次数驱动 0→3 ----------------------------------------------


def _thresholds(registry: SkillRegistry, skill_id: str) -> tuple[int, int, int]:
    return tuple(registry.read_exp_threshold(skill_id, lv) for lv in range(3))  # type: ignore[return-value]


def test_usage_driven_upgrade_0_to_1_step_by_step(
    registry: SkillRegistry,
) -> None:
    """推导：成功施放 +1 exp；累计到 threshold[0] 才升 1 级（差 1 次不升）。"""
    t1, _, _ = _thresholds(registry, "fireball")
    state = SkillState(skill_id="fireball")
    for _ in range(t1 - 1):
        assert grant_cast_exp(state, registry) is False
    assert state.exp == t1 - 1 and state.level == 0
    assert grant_cast_exp(state, registry) is True  # 第 threshold[0] 次 → 升级
    assert state.level == 1 and state.exp == 0


def test_usage_driven_full_path_0_to_3_then_cap(
    registry: SkillRegistry,
) -> None:
    """推导：逐级消耗阈值后升到满级 3；满级后再施放不涨 exp 不越级。"""
    t1, t2, t3 = _thresholds(registry, "healing")
    state = SkillState(skill_id="healing")
    leveled = [grant_cast_exp(state, registry) for _ in range(t1 + t2 + t3)]
    assert state.level == 3
    assert sum(leveled) == 3  # 恰好跨 3 次
    assert leveled[t1 - 1] and leveled[t1 + t2 - 1] and leveled[-1]
    # 满级封顶：继续使用不再累积经验
    for _ in range(5):
        assert grant_cast_exp(state, registry) is False
    assert state.level == 3 and state.exp == 0


def test_burst_grant_crosses_multiple_levels_at_once(
    registry: SkillRegistry,
) -> None:
    """推导：一次批量授经验（如补结算）也应连续跨级并保留溢出余量。"""
    t1, t2, _ = _thresholds(registry, "thunder_bolt")
    state = SkillState(skill_id="thunder_bolt")
    assert grant_cast_exp(state, registry, times=t1 + t2 + 3) is True
    assert state.level == 2 and state.exp == 3


# --- 5. 施放门禁：习得 / MP / CD ------------------------------------------------


@dataclass
class _CastEnv:
    """try_cast 入参打包（减少重复字面量）。"""

    skill_id: str
    char_level: int
    mp: int
    state: SkillState | None
    last_cast_tick: int | None = None
    now_tick: int = 0


def _cast(registry: SkillRegistry, env: _CastEnv) -> CastAttempt:
    return try_cast(
        registry,
        env.skill_id,
        char_level=env.char_level,
        mp=env.mp,
        state=env.state,
        last_cast_tick=env.last_cast_tick,
        now_tick=env.now_tick,
    )


def test_successful_cast_deducts_mp_and_grants_exp(
    registry: SkillRegistry, fixture_data: dict[str, dict[str, Any]]
) -> None:
    """推导：mp - mp_cost 与 exp +1 都来自 fixture 字段，不引入测试常量。"""
    cost = fixture_data["fireball"]["mp_cost"]
    state = SkillState(skill_id="fireball")
    result = _cast(registry, _CastEnv("fireball", 7, mp=cost + 20, state=state))
    assert result.success is True
    assert result.reason == "ok"
    assert result.mp_after == 20
    assert result.exp_gained == 1
    assert state.exp == 1


def test_insufficient_mp_fails_without_deduction_or_exp(
    registry: SkillRegistry, fixture_data: dict[str, dict[str, Any]]
) -> None:
    """验收 #4：MP 不足 → False 且不扣蓝、不涨经验（mp_after 原样返回）。"""
    cost = fixture_data["thunder_bolt"]["mp_cost"]
    state = SkillState(skill_id="thunder_bolt")
    result = _cast(registry, _CastEnv("thunder_bolt", 17, mp=cost - 1, state=state))
    assert result.success is False
    assert result.reason == "insufficient_mp"
    assert result.mp_after == cost - 1  # 一滴没扣
    assert result.exp_gained == 0
    assert state.exp == 0 and state.level == 0  # 经验原地不动


def test_zero_mp_is_insufficient_too(registry: SkillRegistry) -> None:
    """推导：mp_cost > 0 的技能在 mp=0 时同样失败（≥0 门禁的退化边界）。"""
    state = SkillState(skill_id="lightning")
    result = _cast(registry, _CastEnv("lightning", 26, mp=0, state=state))
    assert result.success is False and result.reason == "insufficient_mp"


def test_not_learned_when_below_learn_level_or_missing_state(
    registry: SkillRegistry, fixture_data: dict[str, dict[str, Any]]
) -> None:
    """推导：未习得（等级不够 or 无 skill_state 行）→ 失败且不扣蓝。"""
    cost = fixture_data["ice_thunder"]["mp_cost"]
    below = _cast(registry, _CastEnv("ice_thunder", 34, mp=cost * 10, state=None))
    assert below.success is False and below.reason == "not_learned"
    no_state = _cast(registry, _CastEnv("ice_thunder", 35, mp=cost * 10, state=None))
    assert no_state.success is False and no_state.reason == "not_learned"
    assert no_state.mp_after == cost * 10


def test_cooldown_blocks_between_casts(registry: SkillRegistry) -> None:
    """推导：CD 内（now-last < cooldown_seconds）失败不扣蓝；CD 刚好走完放行。"""
    cd = registry.get("soul_shoulder").cooldown_seconds
    state = SkillState(skill_id="soul_shoulder")
    blocked = _cast(
        registry,
        _CastEnv("soul_shoulder", 30, mp=50, state=state, last_cast_tick=10, now_tick=10 + cd - 1),
    )
    assert blocked.success is False and blocked.reason == "cooldown"
    assert blocked.mp_after == 50
    ready = _cast(
        registry,
        _CastEnv("soul_shoulder", 30, mp=50, state=state, last_cast_tick=10, now_tick=10 + cd),
    )
    assert ready.success is True


def test_free_skill_always_off_cooldown(registry: SkillRegistry) -> None:
    """推导：basic_sword cd=0 → 同 tick 连续施放不受 CD 限制。"""
    state = SkillState(skill_id="basic_sword")
    first = _cast(registry, _CastEnv("basic_sword", 7, mp=5, state=state, now_tick=99))
    second = _cast(
        registry,
        _CastEnv("basic_sword", 7, mp=5, state=state, last_cast_tick=99, now_tick=99),
    )
    assert first.success and second.success


# --- 6. 真实 gamedata 最小集（不断言总数，那是 todo 20）--------------------------


def test_default_registry_loads_real_gamedata_minimum() -> None:
    """推导：task 13 已建 3 个 JSON；只断言已知 id 存在，不断言总数（todo 20 范畴）。"""
    reg = load_registry()
    known = {"basic_sword", "fireball", "healing"}
    assert known <= set(reg.ids())
    for skill_id in known:
        assert reg.get(skill_id).name  # 非空名即可，字段细节归 fixture 用例管
