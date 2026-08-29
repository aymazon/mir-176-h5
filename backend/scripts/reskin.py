"""reskin.py — task 38 改名换皮批量改写器（M5 收口）。

按 docs/reskin-mapping.md 的 id→旧名→新名 三列映射改写展示字段：

- backend/gamedata/{maps,monsters,items,skills,npcs,quests}/*.json 的 ``name``
  字段（按 id 精确改写；**不改 id、不改数值**——引用完整性与数值事实保留）；
- npcs 的 ``dialogue[]`` 与 quests 的 ``dialogue.{accept,progress,complete}``
  自由文本：用「全部实体旧名 + phrases 段旧词」按长度降序做整体替换；
- frontend/src/data/gamedata.ts 的 MAPS/ITEMS 两个常量内的 ``name: "..."``
  字段（按行首条目 id 改写；该文件为 gamedata 的静态快照，无生成脚本，
  由本脚本承担换皮同步）。

用法（cwd=backend）：

    uv run python scripts/reskin.py --dry-run        # 预览 diff 摘要，不写盘
    uv run python scripts/reskin.py                  # 应用改写（幂等，可重跑）
    uv run python scripts/reskin.py --strict         # 有未映射 id / 名称不符即 exit 1
    uv run python scripts/reskin.py --word-list /tmp/reskin-old-names.txt
                                                     # 机械生成验收词表（old != new 全行）

验收词表配合 git grep 使用（豁免清单见 reskin-mapping.md 文件头）：

    git grep -F -f /tmp/reskin-old-names.txt -- backend/gamedata/ frontend/src/ docs/ \
        ':!docs/reskin-mapping.md' ':!docs/MVP/' ':!docs/data-coverage.md'
"""

# pyright: reportUnusedCallResult=false

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
_MAPPING_MD = _REPO_ROOT / "docs" / "reskin-mapping.md"
_FRONTEND_GAMEDATA_TS = _REPO_ROOT / "frontend" / "src" / "data" / "gamedata.ts"

#: 六类实体目录（name 按 id 改写）；levels.json/teleport.json 无展示名，不在列。
CATEGORIES = ("maps", "monsters", "items", "skills", "npcs", "quests")

#: 携带自由文本对话的类别 → 对话字段路径
_DIALOGUE_LIST_FIELDS = {"npcs": ("dialogue",)}
_DIALOGUE_DICT_FIELDS = {"quests": ("dialogue",)}


@dataclass
class Mapping:
    """解析后的映射表：按类别的 id→(old,new)，加 phrases 段。"""

    entities: dict[str, dict[str, tuple[str, str]]] = field(default_factory=dict)
    phrases: list[tuple[str, str]] = field(default_factory=list)

    def phrase_pairs(self) -> list[tuple[str, str]]:
        """自由文本替换对：实体旧名（old != new）+ phrases 段，按旧词长度降序。

        降序保证「比奇书店老板」先于「比奇」、「召唤神兽」先于「神兽」命中，
        避免短词先行替换造成的长词永不匹配。
        """
        pairs: list[tuple[str, str]] = list(self.phrases)
        for per_cat in self.entities.values():
            for old, new in per_cat.values():
                if old != new:
                    pairs.append((old, new))
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        return pairs

    def word_list(self) -> list[str]:
        """机械生成验收词表：全部 old != new 的旧名/旧词（去重，一行一词）。"""
        words = {old for old, _new in self.phrases}
        for per_cat in self.entities.values():
            for old, new in per_cat.values():
                if old != new:
                    words.add(old)
        return sorted(words)

    def new_name_for(self, entry_id: str) -> str | None:
        """跨类别查 id 的新名（frontend 快照 MAPS/ITEMS 改写用）。"""
        for per_cat in self.entities.values():
            hit = per_cat.get(entry_id)
            if hit is not None:
                _old, new = hit
                return new
        return None


def load_reskin_mapping(path: Path = _MAPPING_MD) -> Mapping:
    """解析 reskin-mapping.md：``## <section>`` 切换段，表格行取前三列。"""
    mapping = Mapping()
    section = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            section = line[3:].strip().split("（")[0].split("(")[0].strip()
            continue
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("id", "旧词") or cells[1] in ("旧名", "新词"):
            continue
        if section == "phrases":
            mapping.phrases.append((cells[0], cells[1]))
        elif section in CATEGORIES:
            if len(cells) < 3:
                raise ValueError(f"{path}: {section} 行缺第三列: {line}")
            mapping.entities.setdefault(section, {})[cells[0]] = (cells[1], cells[2])
    return mapping


def _compile_phrase_re(pairs: list[tuple[str, str]]) -> re.Pattern[str]:
    """旧词 alternation 编译：长度降序保证最长优先；单次 sub 防级联二重替换。

    级联坑（首跑实证）：逐对 str.replace 会让「蛆卵收购→穴蛆卵收购」的产物再被
    「蛆卵→穴蛆卵」命中成「穴穴蛆卵」；re.sub 扫描不回顾已替换输出，天然免级联。
    """
    olds = sorted({old for old, _new in pairs}, key=len, reverse=True)
    return re.compile("|".join(re.escape(old) for old in olds))


def _apply_phrases(text: str, pairs: list[tuple[str, str]]) -> str:
    table = dict(pairs)
    return _compile_phrase_re(pairs).sub(lambda m: table[m.group(0)], text)


@dataclass
class Stats:
    renamed: int = 0
    already: int = 0
    dialogue_touched: int = 0
    problems: list[str] = field(default_factory=list)


def _atomic_write(path: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        return
    tmp = path.with_suffix(path.suffix + ".reskin-tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _reskin_gamedata(mapping: Mapping, dry_run: bool, stats: Stats) -> None:
    pairs = mapping.phrase_pairs()
    for category in CATEGORIES:
        cat_map = mapping.entities.get(category, {})
        cat_dir = _BACKEND_ROOT / "gamedata" / category
        for path in sorted(cat_dir.glob("*.json")):
            raw_text = path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            entry_id = str(data.get("id", ""))
            row = cat_map.get(entry_id)
            rel = f"gamedata/{category}/{path.name}"
            changed = False
            if row is None:
                stats.problems.append(f"{rel}: id '{entry_id}' 不在映射表 {category} 段")
            else:
                old, new = row
                name = str(data.get("name", ""))
                if old != new:
                    if name == old:
                        data["name"] = new
                        stats.renamed += 1
                        changed = True
                    elif name == new:
                        stats.already += 1
                    else:
                        stats.problems.append(
                            f"{rel}: name '{name}' 既非旧名 '{old}' 亦非新名 '{new}'"
                        )
            # 自由文本对话：实体旧名 + phrases 降序替换
            for field_name in _DIALOGUE_LIST_FIELDS.get(category, ()):
                dialogue = data.get(field_name)
                if isinstance(dialogue, list):
                    new_lines = [_apply_phrases(str(line), pairs) for line in dialogue]
                    if new_lines != dialogue:
                        data[field_name] = new_lines
                        stats.dialogue_touched += 1
                        changed = True
            for field_name in _DIALOGUE_DICT_FIELDS.get(category, ()):
                dialogue = data.get(field_name)
                if isinstance(dialogue, dict):
                    new_dict = {
                        str(k): _apply_phrases(str(v), pairs) for k, v in dialogue.items()
                    }
                    if new_dict != dialogue:
                        data[field_name] = new_dict
                        stats.dialogue_touched += 1
                        changed = True
            if changed:
                out = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
                _atomic_write(path, out, dry_run)


#: 行首条目 id + 行内首个 name 字段（frontend gamedata.ts 的 MAPS/ITEMS 每行一条目）
_TS_ENTRY_RE = re.compile(r'^(\s*"([^"]+)":\s*\{.*?name:\s*")[^"]*(".*)$')


def _reskin_frontend_snapshot(mapping: Mapping, dry_run: bool, stats: Stats) -> None:
    path = _FRONTEND_GAMEDATA_TS
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out_lines: list[str] = []
    for line in lines:
        match = _TS_ENTRY_RE.match(line)
        if match:
            entry_id = match.group(2)
            new = mapping.new_name_for(entry_id)
            if new is not None and match.group(0) is not None:
                replaced = f"{match.group(1)}{new}{match.group(3)}\n"
                if replaced != line:
                    stats.renamed += 1
                out_lines.append(replaced)
                continue
        out_lines.append(line)
    new_text = "".join(out_lines)
    if new_text != "".join(lines):
        _atomic_write(path, new_text, dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按 docs/reskin-mapping.md 批量换皮")
    parser.add_argument("--dry-run", action="store_true", help="只打印摘要，不写盘")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="未映射 id / 名称不符时 exit 1",
    )
    parser.add_argument("--word-list", type=Path, default=None, help="输出验收词表路径")
    args = parser.parse_args(argv)

    mapping = load_reskin_mapping()

    if args.word_list is not None:
        words = mapping.word_list()
        args.word_list.write_text("\n".join(words) + "\n", encoding="utf-8")
        print(f"[word-list] {len(words)} 词 → {args.word_list}")

    stats = Stats()
    _reskin_gamedata(mapping, args.dry_run, stats)
    _reskin_frontend_snapshot(mapping, args.dry_run, stats)

    mode = "dry-run" if args.dry_run else "apply"
    print(f"[reskin:{mode}] renamed={stats.renamed} already={stats.already} "
          f"dialogue_touched={stats.dialogue_touched} problems={len(stats.problems)}")
    for problem in stats.problems:
        print(f"  [problem] {problem}")
    if stats.problems and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
