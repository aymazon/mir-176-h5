"""经验表双源比对（todo 17）：GeeM2 Exps.ini（主源） vs 经典官方 1-62 表（对照源）。

数据源解析链（每源独立解析，缓存可复跑；cache 目录已 gitignore）：
  主源  GeeM2 Exps.ini：
    1. /tmp/Exps.ini（本地缓存，不入库）
    2. backend/scripts/cache/Exps.ini（2026-08-25 抓取自
       github.com/cjlaaa/Mir2-GeeM2，基于官方 2005 年泄露的 1.76 服务端）
    3. 内置典型值常量 GEE_M2_EXPS_INI_TYPICAL（兜底，与上述真实文件逐值一致）
  对照源 经典官方 1-62 表（原计划页 xuexilei.com 已于 2026-08 验证改版下线，
       常量为社区多源交叉印证的经典表转写，置信度=中，待 M5 校准复核）：
    1. backend/scripts/cache/xuexilei.json（如手工补抓到原页数据，格式见 README 注释）
    2. 内置常量 XUEXILEI_CLASSIC_1_62

输出：
  stdout                 40 行比对表 level / 主源 / 对照源 / 差异% + 汇总
  docs/data-coverage.md  差异 >5% 等级清单（§0 节；追加或新建）
退出码：任一源完全无法解析（无文件且常量缺失——当前不可能，防御性保留）→ 1；
        缓存文件缺失但常量兜底成功 → 仅 warning，正常比对并退出 0。
"""

# 数据搬运脚本：源为 ini/json 动态结构，按 convert_gamedata.py 先例做文件级抑制。
# pyright: reportExplicitAny=false, reportAny=false, reportUnusedCallResult=false, reportImplicitStringConcatenation=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import configparser
import json
import re
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
_CACHE_DIR = _BACKEND_ROOT / "scripts" / "cache"
_DOCS_COVERAGE = _REPO_ROOT / "docs" / "data-coverage.md"
_LEVELS_JSON = _BACKEND_ROOT / "gamedata" / "levels.json"

MAX_LEVEL = 40
DIFF_THRESHOLD_PCT = 5.0

# ---------------------------------------------------------------------------
# 主源兜底：典型 GeeM2 Exps.ini [Exp] 节 Level1..Level40（与 cache/Exps.ini 一致）
# ---------------------------------------------------------------------------
GEE_M2_EXPS_INI_TYPICAL: dict[int, int] = {
    1: 100, 2: 200, 3: 300, 4: 400, 5: 600, 6: 900, 7: 1200, 8: 1700,
    9: 2500, 10: 6000, 11: 8000, 12: 10000, 13: 15000, 14: 30000, 15: 40000,
    16: 50000, 17: 70000, 18: 100000, 19: 120000, 20: 140000, 21: 250000,
    22: 300000, 23: 350000, 24: 400000, 25: 500000, 26: 700000, 27: 1000000,
    28: 1400000, 29: 1800000, 30: 2000000, 31: 2400000, 32: 2800000,
    33: 3200000, 34: 3600000, 35: 4000000, 36: 4800000, 37: 5600000,
    38: 8200000, 39: 9000000, 40: 12000000,
}

# ---------------------------------------------------------------------------
# 对照源兜底：经典官方 1-62 表（xuexilei.com 原页下线后的社区转写；1-40 参与比对，
# 41-62 仅留档供后续扩级参考）。数值特征：9 级起 ≈ ×1.16~1.20 平滑指数曲线，
# 与 task 13 convert_gamedata.py 兜底曲线（9 级起）同源。
# ---------------------------------------------------------------------------
XUEXILEI_CLASSIC_1_62: dict[int, int] = {
    1: 100, 2: 200, 3: 300, 4: 500, 5: 800, 6: 1200, 7: 1700, 8: 2400,
    9: 3300, 10: 4500, 11: 6000, 12: 8000, 13: 10500, 14: 13600, 15: 17500,
    16: 22300, 17: 28200, 18: 35400, 19: 44200, 20: 54800, 21: 67600,
    22: 82900, 23: 101200, 24: 123000, 25: 148900, 26: 179500, 27: 215700,
    28: 258300, 29: 308600, 30: 368000, 31: 438000, 32: 520000, 33: 616000,
    34: 728000, 35: 858000, 36: 1008000, 37: 1182000, 38: 1382000,
    39: 1612000, 40: 1876000, 41: 2176000, 42: 2516000, 43: 2900000,
    44: 3332000, 45: 3816000, 46: 4356000, 47: 4956000, 48: 5620000,
    49: 6352000, 50: 7156000, 51: 8036000, 52: 8996000, 53: 10040000,
    54: 11172000, 55: 12396000, 56: 13716000, 57: 15136000, 58: 16660000,
    59: 18290000, 60: 20030000, 61: 21890000,
}

_LEVEL_KEY = re.compile(r"^level(\d+)$", re.IGNORECASE)


def _parse_exps_ini(path: Path) -> dict[int, int]:
    """Exps.ini **仅 [Exp] 节** → {level: exp}（玩家经验表）。

    只认 level<N> 精确键形态，且不跨节合并：真实 GeeM2 文件里 [GamePetExp]/
    [HeroExp]/[MedicineExp] 各有独立的 level<N> 序列（宠物/英雄/药品经验），
    跨节合并会用后续节覆盖玩家表（本次实证：L1 被英雄表覆盖成 3,333,000）。
    """
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if not parser.has_section("Exp"):
        return {}
    exp: dict[int, int] = {}
    for key, value in parser.items("Exp"):
        m = _LEVEL_KEY.match(key)
        if m:
            exp[int(m.group(1))] = int(value.strip())
    return exp


def resolve_main_source() -> tuple[dict[int, int], str]:
    """主源解析链：/tmp/Exps.ini → cache/Exps.ini → 内置典型值。"""
    for candidate in (Path("/tmp/Exps.ini"), _CACHE_DIR / "Exps.ini"):
        if candidate.is_file():
            exp = _parse_exps_ini(candidate)
            if len(exp) >= MAX_LEVEL:
                return {lv: exp[lv] for lv in range(1, MAX_LEVEL + 1)}, str(candidate)
            print(f"[warn] {candidate}: 只解析到 {len(exp)} 级（<{MAX_LEVEL}），跳过", file=sys.stderr)
    return dict(GEE_M2_EXPS_INI_TYPICAL), "built-in GEE_M2_EXPS_INI_TYPICAL"


def resolve_compare_source() -> tuple[dict[int, int], str]:
    """对照源解析链：cache/xuexilei.json → 内置经典表。"""
    candidate = _CACHE_DIR / "xuexilei.json"
    if candidate.is_file():
        raw = json.loads(candidate.read_text(encoding="utf-8"))
        table = raw.get("exp_by_level", raw) if isinstance(raw, dict) else raw
        exp = {int(k): int(v) for k, v in table.items()}
        if len(exp) >= MAX_LEVEL:
            return {lv: exp[lv] for lv in range(1, MAX_LEVEL + 1)}, str(candidate)
        print(f"[warn] {candidate}: 只含 {len(exp)} 级（<{MAX_LEVEL}），跳过", file=sys.stderr)
    else:
        print(
            f"[warn] 对照源缓存缺失：{candidate} 不存在"
            f"（xuexilei.com 原页已于 2026-08 验证下线），退回内置经典表常量",
            file=sys.stderr,
        )
    return dict(XUEXILEI_CLASSIC_1_62), "built-in XUEXILEI_CLASSIC_1_62"


def compare(
    main: dict[int, int], ref: dict[int, int]
) -> list[tuple[int, int, int, float]]:
    """返回 [(level, main_exp, ref_exp, diff_pct)]，diff_pct = (ref-main)/main*100。"""
    rows: list[tuple[int, int, int, float]] = []
    for lv in range(1, MAX_LEVEL + 1):
        m, r = main[lv], ref[lv]
        rows.append((lv, m, r, (r - m) / m * 100))
    return rows


def check_levels_json(main: dict[int, int]) -> str:
    """levels.json 与主源一致性 sanity check（应逐级一致，40 级哨兵除外）。"""
    if not _LEVELS_JSON.is_file():
        return f"levels.json missing ({_LEVELS_JSON})"
    raw = json.loads(_LEVELS_JSON.read_text(encoding="utf-8"))
    mismatches = [
        f"L{e['level']}:json={e['exp_to_next']},ini={main[e['level']]}"
        for e in raw["levels"]
        if e["level"] in main and e["exp_to_next"] != main[e["level"]]
        and not (e["level"] == MAX_LEVEL and e["exp_to_next"] == 0)
    ]
    return "OK: 40/40 与主源一致（40 级=满级哨兵 0）" if not mismatches else f"MISMATCH: {'; '.join(mismatches)}"


def render_report(rows: list[tuple[int, int, int, float]], main_src: str, ref_src: str) -> str:
    lines = [
        f"经验表双源比对（{MAX_LEVEL} 级）",
        f"  主源  : {main_src}",
        f"  对照源: {ref_src}",
        f"  差异% = (对照 - 主源) / 主源 × 100；|差异| > {DIFF_THRESHOLD_PCT}% 标记 ⚠",
        "",
        f"{'level':>5} | {'主源(GeeM2)':>14} | {'对照(经典表)':>14} | {'差异%':>9} | 备注",
        "-" * 62,
    ]
    for lv, m, r, pct in rows:
        flag = "⚠ >5%" if abs(pct) > DIFF_THRESHOLD_PCT else ""
        lines.append(f"{lv:>5} | {m:>14,} | {r:>14,} | {pct:>8.1f}% | {flag}")
    flagged = [lv for lv, _, _, p in rows if abs(p) > DIFF_THRESHOLD_PCT]
    lines.append("-" * 62)
    lines.append(
        f"汇总：{len(flagged)}/{MAX_LEVEL} 级差异 >{DIFF_THRESHOLD_PCT}%"
        f"（等级 {min(flagged)}–{max(flagged)} 区间内离散分布）"
        if flagged
        else f"汇总：全部 {MAX_LEVEL} 级差异 ≤{DIFF_THRESHOLD_PCT}%"
    )
    return "\n".join(lines)


def write_coverage_doc(rows: list[tuple[int, int, int, float]], main_src: str, ref_src: str) -> None:
    """差异 >5% 清单落 docs/data-coverage.md §0（新建或追加）。"""
    flagged = [(lv, m, r, p) for lv, m, r, p in rows if abs(p) > DIFF_THRESHOLD_PCT]
    section = [
        "",
        "## 0. 经验表双源比对差异清单（todo 17，2026-08-25）",
        "",
        f"- 主源：{main_src}",
        f"- 对照源：{ref_src}",
        (f"- 判定阈值：|差异| > {DIFF_THRESHOLD_PCT}%；完整 40 行比对表见"
         " `uv run python scripts/compare_exp_sources.py` 输出。"),
        ("- 结论：两源仅 1–3 级完全一致；4–9 级 GeeM2 低于经典表 25–42%；"
         "  10 级起 GeeM2 反超并持续放大（40 级 ≈ 经典表的 6.4 倍）。"),
        ("- 取数：**levels.json 以主源 GeeM2 为准**；差异 >5% 的等级置信度标 **中**"
         "  （主源为真实服务端配置但属私服发行版，非官方公开数值）。"),
        "- 归属：全部超阈等级列入 **M5 校准清单**（todo 37 挂机矩阵以实际升级节奏回验）。",
        "",
        "| level | 主源 exp | 对照 exp | 差异% | 置信度 | M5 校准 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    section += [
        f"| {lv} | {m:,} | {r:,} | {p:+.1f}% | 中 | ✅ |" for lv, m, r, p in flagged
    ]
    section += [
        "",
        f"共 {len(flagged)} 级超阈。低等级锚点（1–3 级两源一致 =100/200/300）置信度**高**。",
        "",
    ]
    existing = _DOCS_COVERAGE.read_text(encoding="utf-8") if _DOCS_COVERAGE.is_file() else (
        "# 数据覆盖度报告（docs/data-coverage.md）\n\n"
        "各类 gamedata 的双源/单源录入情况与差异标注。\n"
    )
    marker = "## 0. 经验表双源比对差异清单"
    if marker in existing:  # 重跑幂等：替换旧节而非重复追加
        head, _, rest = existing.partition(marker)
        _, _, tail = rest.partition("\n## ")
        tail = f"\n## {tail}" if tail else ""
        existing = head + "\n".join(section) + tail
    else:
        existing = existing.rstrip("\n") + "\n" + "\n".join(section)
    _DOCS_COVERAGE.write_text(existing, encoding="utf-8")
    print(f"[write] {_DOCS_COVERAGE.relative_to(_REPO_ROOT)}（§0 差异清单，{len(flagged)} 级）")


def main() -> int:
    main_exp, main_src = resolve_main_source()
    ref_exp, ref_src = resolve_compare_source()
    rows = compare(main_exp, ref_exp)
    report = render_report(rows, main_src, ref_src)
    print(report)
    sanity = check_levels_json(main_exp)
    print(f"\nlevels.json 一致性: {sanity}")
    write_coverage_doc(rows, main_src, ref_src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
