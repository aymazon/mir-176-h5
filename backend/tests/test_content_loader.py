"""Fail-fast content loader tests (plan task 13, W2T1).

Negative cases copy the real ``game_data/`` files into ``tmp_path`` and
corrupt exactly one file at a time; the real directory is never modified.
The happy path loads the real ``game_data/`` read-only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.content.loader import CONTENT_MODELS, Content, ContentError, load_all_content
from app.content.models import ClassesFile, ExpCurve, ItemsFile, SkillsFile

DATA_DIR = Path(__file__).resolve().parents[1] / "game_data"
REGISTERED: tuple[str, ...] = tuple(CONTENT_MODELS)


def _raw(name: str) -> dict[str, Any]:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _write_content_dir(
    tmp_path: Path,
    *,
    skip: frozenset[str] = frozenset(),
    mutators: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
    extra: dict[str, object] | None = None,
) -> Path:
    """Copy the four registered files into a fresh dir, with optional edits."""
    target = tmp_path / "content"
    target.mkdir()
    mutators = mutators or {}
    for filename in REGISTERED:
        if filename in skip:
            continue
        raw = _raw(filename)
        if filename in mutators:
            raw = mutators[filename](raw)
        (target / filename).write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    for name, payload in (extra or {}).items():
        (target / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def _drop_last_exp_level(raw: dict[str, Any]) -> dict[str, Any]:
    raw["levels"].pop()
    return raw


def _drop_last_class(raw: dict[str, Any]) -> dict[str, Any]:
    raw["classes"].pop()
    return raw


def _unknown_skill_game_class(raw: dict[str, Any]) -> dict[str, Any]:
    raw["skills"][0]["class"] = "rogue"
    return raw


def _invalid_item_slot(raw: dict[str, Any]) -> dict[str, Any]:
    item = next(i for i in raw["items"] if i.get("slot") is not None)
    item["slot"] = "boots"
    return raw


@pytest.mark.parametrize("filename", REGISTERED)
def test_missing_registered_file_raises_naming_file(tmp_path: Path, filename: str) -> None:
    target = _write_content_dir(tmp_path, skip=frozenset({filename}))
    with pytest.raises(ContentError, match=filename):
        load_all_content(target)


@pytest.mark.parametrize("filename", REGISTERED)
def test_invalid_json_raises_naming_file(tmp_path: Path, filename: str) -> None:
    target = _write_content_dir(tmp_path)
    (target / filename).write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(ContentError, match=filename):
        load_all_content(target)


@pytest.mark.parametrize(
    ("filename", "mutate"),
    [
        pytest.param("exp_curve.json", _drop_last_exp_level, id="dangling-level"),
        pytest.param("classes.json", _drop_last_class, id="missing-class"),
        pytest.param("skills.json", _unknown_skill_game_class, id="unknown-game-class"),
        pytest.param("items.json", _invalid_item_slot, id="invalid-slot"),
    ],
)
def test_schema_violation_raises_naming_file(
    tmp_path: Path, filename: str, mutate: Callable[[dict[str, Any]], dict[str, Any]]
) -> None:
    target = _write_content_dir(tmp_path, mutators={filename: mutate})
    with pytest.raises(ContentError, match=filename):
        load_all_content(target)


def test_unregistered_json_warns_on_stderr_but_loads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _write_content_dir(tmp_path, extra={"monsters.json": {"monsters": []}})
    content = load_all_content(target)
    captured = capsys.readouterr()
    assert "monsters.json" in captured.err
    assert "skipping" in captured.err
    assert content.extras == {}


def test_real_game_data_validates_into_named_fields() -> None:
    content = load_all_content(DATA_DIR)
    assert isinstance(content, Content)
    assert isinstance(content.exp_curve, ExpCurve)
    assert isinstance(content.classes, ClassesFile)
    assert isinstance(content.skills, SkillsFile)
    assert isinstance(content.items, ItemsFile)
    assert [entry.level for entry in content.exp_curve.levels] == list(range(1, 40))
    assert sorted(cls.code for cls in content.classes.classes) == ["mage", "taoist", "warrior"]
    assert len(content.skills.skills) == len(_raw("skills.json")["skills"])
    assert len(content.items.items) == len(_raw("items.json")["items"])
    assert content.extras == {}
