"""Fail-fast loader for the static content tables under ``game_data/``.

Every file registered in :data:`CONTENT_MODELS` must exist, parse as JSON,
and validate against its canonical pydantic model (see
:mod:`app.content.models`). Any failure raises :class:`ContentError` naming
the offending file, so the caller (``python -m seed``, server startup)
exits at boot with an actionable diagnostic (W2T1 / plan task 13).

Extensibility contract: a later wave adds its model plus ONE
filename -> model entry in :data:`CONTENT_MODELS` and the loader picks it
up automatically (validated tables land in :attr:`Content.extras` until
promoted to a named field). Unregistered ``*.json`` files are skipped with
a stderr warning so partial waves never block startup.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ValidationError

from app.content.models import (
    ClassesFile,
    ExpCurve,
    ItemsFile,
    MapConnectionsFile,
    MapsFile,
    MonstersFile,
    SkillsFile,
    SpawnsFile,
)

__all__ = ["CONTENT_MODELS", "Content", "ContentError", "load_all_content"]


class ContentError(RuntimeError):
    """A content file is missing, unparseable, or fails schema validation."""


#: filename -> validating model. Later waves extend this mapping only;
#: ``load_all_content`` picks up new entries automatically.
CONTENT_MODELS: Final[dict[str, type[BaseModel]]] = {
    "exp_curve.json": ExpCurve,
    "classes.json": ClassesFile,
    "skills.json": SkillsFile,
    "items.json": ItemsFile,
    "maps.json": MapsFile,
    "monsters.json": MonstersFile,
    "spawns.json": SpawnsFile,
    "map_connections.json": MapConnectionsFile,
}

#: Registered files mapped to a named :class:`Content` field; any other
#: registered file is exposed through :attr:`Content.extras`.
_NAMED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "exp_curve.json", "classes.json", "skills.json", "items.json",
        "maps.json", "monsters.json", "spawns.json", "map_connections.json",
    }
)


@dataclass(frozen=True, slots=True)
class Content:
    """Validated game content: core numeric tables plus world tables (maps/monsters/spawns/connections)."""

    exp_curve: ExpCurve
    classes: ClassesFile
    skills: SkillsFile
    items: ItemsFile
    maps: MapsFile
    monsters: MonstersFile
    spawns: SpawnsFile
    map_connections: MapConnectionsFile
    extras: dict[str, BaseModel] = field(default_factory=dict)


def _load_one[T: BaseModel](data_dir: Path, filename: str, model: type[T]) -> T:
    path = data_dir / filename
    if not path.is_file():
        msg = f"missing content file: {path}"
        raise ContentError(msg)
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"{filename}: unreadable or invalid JSON: {exc}"
        raise ContentError(msg) from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        errors = exc.errors()
        details = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in errors[:5]
        )
        if len(errors) > 5:
            details += f" (+{len(errors) - 5} more)"
        msg = f"{filename}: schema validation failed: {details}"
        raise ContentError(msg) from exc


def _as[T: BaseModel](value: BaseModel, model: type[T], filename: str) -> T:
    """Narrow a validated table to its registry type (guards registry typos)."""
    if not isinstance(value, model):
        msg = (
            f"internal error: {filename} validated as {type(value).__name__}, "
            f"expected {model.__name__}"
        )
        raise ContentError(msg)
    return value


def load_all_content(data_dir: Path) -> Content:
    """Load and validate every registered content file in ``data_dir``.

    Raises:
        ContentError: on the first missing file, parse error, or schema
            violation; the message names the offending file.
    """
    tables: dict[str, BaseModel] = {}
    for filename, model in CONTENT_MODELS.items():
        tables[filename] = _load_one(data_dir, filename, model)

    for path in sorted(data_dir.glob("*.json")):
        if path.name not in CONTENT_MODELS:
            print(f"content: skipping unregistered file {path.name} (no model yet)", file=sys.stderr)

    return Content(
        exp_curve=_as(tables["exp_curve.json"], ExpCurve, "exp_curve.json"),
        classes=_as(tables["classes.json"], ClassesFile, "classes.json"),
        skills=_as(tables["skills.json"], SkillsFile, "skills.json"),
        items=_as(tables["items.json"], ItemsFile, "items.json"),
        maps=_as(tables["maps.json"], MapsFile, "maps.json"),
        monsters=_as(tables["monsters.json"], MonstersFile, "monsters.json"),
        spawns=_as(tables["spawns.json"], SpawnsFile, "spawns.json"),
        map_connections=_as(
            tables["map_connections.json"], MapConnectionsFile, "map_connections.json"
        ),
        extras={name: table for name, table in tables.items() if name not in _NAMED_FIELDS},
    )
