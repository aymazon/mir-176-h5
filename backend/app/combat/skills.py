"""技能注册表 / 施放门禁 / 技能熟练度升级纯函数（todo 11）。

职责边界（docs/MVP/README.md §4、tests/combat/test_skills.py 被测契约）：
- 注册表是 gamedata（或测试 fixture）的**只读投影**：构造时经 ``SkillDef``
  pydantic 校验（extra="forbid" 拦未知字段），之后只做查询；
- 施放决策链三道闸：未习得（等级不够 or 无 SkillState 行）→ 冷却 → MP 不足，
  任一闸失败都**不扣蓝、不涨经验**（验收红线，mp_after 原样返回）；
- 熟练度升级：成功施放 +1 exp，累计到 ``level_exp_required[level]`` 即跨级
  （exp 清零、level+1），满级封顶后不再累积。

纪律：
- 纯函数/纯查询：不读 DB、不写库、无随机源；文件 IO 仅限 ``load_registry``
  显式传入路径或默认 gamedata 目录（JSON 数据文件，非 DB）；
- 数值系数唯一来源 ``app.combat.balance``——本文件禁止小数字面量
  （tests/test_balance.py 有 grep 防回归断言）；技能倍率本身是 gamedata
  数据（fixture/gamedata/skills/*.json），经 ``get_multiplier`` 读出；
- 不实现 1.8+ 技能（开天斩等不在 MVP gamedata，get 会 fail-fast）。
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from app.combat.balance import SKILL_MULTIPLIER_BASE
from app.gamedata_schema.skills import SkillClass, SkillDef

__all__ = [
    "CastAttempt",
    "SkillNotFoundError",
    "SkillRegistry",
    "SkillState",
    "grant_cast_exp",
    "load_registry",
    "try_cast",
]

# 施放结果 reason 的封闭集合："ok" 之外全是失败原因。
CastFailReason = Literal["ok", "not_learned", "cooldown", "insufficient_mp"]

# 默认 gamedata 根：backend/gamedata/skills/*.json（与 app/gamedata_loader.py
# 的 DEFAULT_GAMEDATA_ROOT 同源；本文件只取 skills 子目录，避免整包交叉校验）。
DEFAULT_SKILLS_DIR: Final[Path] = (
    Path(__file__).resolve().parents[2] / "gamedata" / "skills"
)


class SkillNotFoundError(LookupError):
    """未知技能 id（fixture 与真实 gamedata 均无）；message 携带该 id。"""


@dataclass(frozen=True, slots=True)
class CastAttempt:
    """单次施放的结算结果（不可变值对象，供 sim 层/SSE 广播消费）。

    失败时 ``mp_after`` 原样返回入参 mp（一滴没扣）、``exp_gained`` 为 0。
    """

    success: bool
    reason: CastFailReason
    mp_after: int
    exp_gained: int


@dataclass(slots=True)
class SkillState:
    """单技能熟练度状态（角色技能行的战斗投影；exp/level 由战斗层就地推进）。

    可变是有意的：``grant_cast_exp`` / ``try_cast`` 成功路径直接累加，
    持久化由上层仓储负责（本模块不碰 DB）。
    """

    skill_id: str
    exp: int = 0
    level: int = 0


class SkillRegistry:
    """SkillDef 只读注册表：构造即全量 pydantic 校验，之后仅查询。"""

    def __init__(self, skills: Mapping[str, Mapping[str, Any]]) -> None:
        self._skills: dict[str, SkillDef] = {}
        for raw in skills.values():
            skill = SkillDef.model_validate(raw)
            if skill.id in self._skills:
                raise ValueError(f"duplicate skill id: {skill.id}")
            self._skills[skill.id] = skill

    # --- 查询 ---------------------------------------------------------------

    def get(self, skill_id: str) -> SkillDef:
        """按 id 取定义；未知 id → SkillNotFoundError（fail-fast 防拼错静默）。"""
        try:
            return self._skills[skill_id]
        except KeyError:
            raise SkillNotFoundError(f"unknown skill id: {skill_id}") from None

    def ids(self) -> tuple[str, ...]:
        """全部技能 id（排序稳定，供断言/遍历）。"""
        return tuple(sorted(self._skills))

    def ids_by_class(self, class_name: str) -> tuple[str, ...]:
        """按职业过滤 id 集合（供 sim 自动战斗选技能）。"""
        cls = SkillClass(class_name)
        return tuple(
            sorted(
                skill_id
                for skill_id, skill in self._skills.items()
                if skill.class_ == cls
            )
        )

    def __contains__(self, skill_id: object) -> bool:
        return skill_id in self._skills

    def __len__(self) -> int:
        return len(self._skills)

    # --- 规则投影 -------------------------------------------------------------

    def can_learn(self, skill_id: str, char_level: int) -> bool:
        """习得门槛：角色等级 ≥ learn_level（成功率条件之一的外部契约）。"""
        return char_level >= self.get(skill_id).learn_level

    def read_exp_threshold(self, skill_id: str, level: int) -> int:
        """跨级阈值：从 level 升到 level+1 所需累计经验。

        满级（level ≥ max_level）返回哨兵 0——满级无阈值，调用方据此封顶。
        """
        if level < 0:
            raise ValueError(f"level 必须 ≥ 0，实得 {level}")
        skill = self.get(skill_id)
        if level >= skill.max_level:
            return 0
        return skill.level_exp_required[level]

    def get_multiplier(self, skill_id: str, level: int) -> float:
        """伤害倍率：每级覆盖键 ``<key>_l<level>`` 优先，回落标量键。

        - 标量恒定型（fireball 只有 damage_multiplier）：各级同值；
        - 每级覆盖型（liehuo 的 damage_multiplier_l1/l2）：按级取覆盖键；
        - 无倍率键的技能（纯辅助/召唤）回落 balance 的 SKILL_MULTIPLIER_BASE。
        """
        params = self.get(skill_id).effect_params
        override = params.get(f"damage_multiplier_l{level}")
        if override is not None:
            return float(override)
        scalar = params.get("damage_multiplier")
        if scalar is not None:
            return float(scalar)
        return SKILL_MULTIPLIER_BASE


def try_cast(
    registry: SkillRegistry,
    skill_id: str,
    *,
    char_level: int,
    mp: int,
    state: SkillState | None,
    last_cast_tick: int | None = None,
    now_tick: int = 0,
) -> CastAttempt:
    """施放决策链：未习得 → 冷却 → MP 三道闸，全过才扣蓝 +1 熟练经验。

    纯函数语义：除成功路径对 ``state.exp/level`` 的熟练度推进外无任何副作用；
    任一闸失败不扣蓝、不涨经验（验收红线）。CD 判定为
    ``now_tick - last_cast_tick < cooldown_seconds``（刚好走完即放行，
    cd=0 的免费技同 tick 连发不受限）。
    """
    skill = registry.get(skill_id)  # 未知 id → SkillNotFoundError
    # 闸 1：未习得（角色等级不够 or 无该技能的 state 行）。
    if state is None or char_level < skill.learn_level:
        return CastAttempt(False, "not_learned", mp, 0)
    # 闸 2：冷却中。
    if last_cast_tick is not None and now_tick - last_cast_tick < skill.cooldown_seconds:
        return CastAttempt(False, "cooldown", mp, 0)
    # 闸 3：MP 不足。
    if mp < skill.mp_cost:
        return CastAttempt(False, "insufficient_mp", mp, 0)
    _levelup = grant_cast_exp(state, registry)  # 成功施放 +1 熟练经验（可能跨级；升级与否不影响本次结算）
    return CastAttempt(True, "ok", mp - skill.mp_cost, 1)


def grant_cast_exp(
    state: SkillState,
    registry: SkillRegistry,
    *,
    times: int = 1,
) -> bool:
    """熟练度经验累加 + 连续跨级判定；返回本次是否发生了升级。

    - 每次施放 +1（批量补结算用 ``times``）；累计达 threshold[level] 即
      跨级：exp 扣减阈值余量滚存、level+1，可一次连跨多级；
    - 满级封顶：不再累积经验（exp 归零），重复调用恒 False；
    - 未知技能 id → SkillNotFoundError（与 get/cast 同一 fail-fast 语义）。
    """
    if times < 0:
        raise ValueError(f"times 必须 ≥ 0，实得 {times}")
    skill = registry.get(state.skill_id)
    if times == 0 or state.level >= skill.max_level:
        return False
    state.exp += times
    leveled = False
    while state.level < skill.max_level:
        threshold = skill.level_exp_required[state.level]
        if state.exp < threshold:
            break
        state.exp -= threshold
        state.level += 1
        leveled = True
    if state.level >= skill.max_level:
        state.exp = 0  # 满级封顶：溢出余量不保留
    return leveled


def load_registry(
    source: Mapping[str, dict[str, Any]] | Path | str | None = None,
) -> SkillRegistry:
    """构建注册表：缺省扫真实 gamedata/skills/*.json；也收 dict 或 JSON 路径。

    - ``None``：默认 ``backend/gamedata/skills/``（MVP 最小集，todo 20 全量）；
    - ``Mapping``：id → 原始字段字典（如 tests/fixtures/skills_32.json 反序列化）；
    - ``Path | str``：单个 JSON 文件（id→def 映射）或含 *.json 的目录。
    文件 IO 只发生在本函数（其余 API 纯内存查询）。
    """
    if source is None:
        return _load_dir(DEFAULT_SKILLS_DIR)
    if isinstance(source, Mapping):
        return SkillRegistry(source)
    path = Path(source)
    if path.is_dir():
        return _load_dir(path)
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"skills JSON 顶层必须是 id→def 映射: {path}")
    return SkillRegistry(raw)


def _load_dir(directory: Path) -> SkillRegistry:
    """扫描目录下全部 *.json（键取文件 stem；注册表最终以 SkillDef.id 为准）。"""
    data: dict[str, Any] = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    }
    return SkillRegistry(data)

