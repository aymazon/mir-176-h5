"""Typed WebSocket message catalog (MVP-DESIGN section 3.6) and the JSON codec.

Every frame carries a ``t`` discriminator. C2S frames are parsed from the wire
by :func:`parse_message` (unknown ``t`` or invalid payloads yield ``None`` so
the gateway degrades gracefully); S2C frames are serialized by
:func:`encode_message`. Field names follow the design's catalog exactly, so
the H5 frontend (W1T6/W1T7) can match them one-to-one.
"""

from __future__ import annotations

import json
from typing import ClassVar, Final, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

# Opaque client payloads (cmd/npc args, event data): any JSON value, typed.
type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]

_C2S_CONFIG: Final[ConfigDict] = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)
_S2C_CONFIG: Final[ConfigDict] = ConfigDict(frozen=True, populate_by_name=True)


class AuthMsg(BaseModel):
    """C2S: first frame after connect; one-time ws_token plus a device tag."""

    model_config: ClassVar[ConfigDict] = _C2S_CONFIG
    t: Literal["auth"] = "auth"
    ws_token: str = Field(min_length=8, max_length=128)
    device: str = Field(default="", max_length=64)


class SelectCharMsg(BaseModel):
    """C2S: enter the game with one of the account's characters (one per connection)."""

    model_config: ClassVar[ConfigDict] = _C2S_CONFIG
    t: Literal["select_char"] = "select_char"
    char_id: int = Field(validation_alias=AliasChoices("char_id", "character_id"))


class MoveMsg(BaseModel):
    """C2S: click-to-move intent; the server validates reachability and may reject."""

    model_config: ClassVar[ConfigDict] = _C2S_CONFIG
    t: Literal["move"] = "move"
    x: int
    y: int


class ChatMsg(BaseModel):
    """C2S: global chat, at most 100 characters (sensitive-word filter lands later)."""

    model_config: ClassVar[ConfigDict] = _C2S_CONFIG
    t: Literal["chat"] = "chat"
    text: str = Field(min_length=1, max_length=100)


class LogoutCharMsg(BaseModel):
    """C2S: leave the world; the character is saved and the connection closes."""

    model_config: ClassVar[ConfigDict] = _C2S_CONFIG
    t: Literal["logout_char"] = "logout_char"


class CmdMsg(BaseModel):
    """C2S: manual fallback commands (drinks, scrolls, auto-battle config; W2/W3)."""

    model_config: ClassVar[ConfigDict] = _C2S_CONFIG
    t: Literal["cmd"] = "cmd"
    action: str = Field(min_length=1, max_length=32)
    args: JsonValue | None = None


class NpcMsg(BaseModel):
    """C2S: NPC dialog actions (buy, sell, repair, teleport, quest; W3)."""

    model_config: ClassVar[ConfigDict] = _C2S_CONFIG
    t: Literal["npc"] = "npc"
    npc_id: str = Field(min_length=1, max_length=32)
    action: str = Field(min_length=1, max_length=32)
    args: JsonValue | None = None


class PingMsg(BaseModel):
    """C2S: client heartbeat (client cadence is 15 s per design 3.4)."""

    model_config: ClassVar[ConfigDict] = _C2S_CONFIG
    t: Literal["ping"] = "ping"


Inbound = (
    AuthMsg
    | SelectCharMsg
    | MoveMsg
    | ChatMsg
    | LogoutCharMsg
    | CmdMsg
    | NpcMsg
    | PingMsg
)


class CharacterState(BaseModel):
    """Full character state.

    Mutable on purpose: the owning session tracks the live world position
    (x/y/map_id) across snapshots and persists the rest on save.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    id: int
    name: str
    character_class: str = Field(alias="class")
    level: int
    exp: int
    hp: int
    mp: int
    gold: int
    bound_city: str
    map_id: str
    x: int
    y: int


class EntityState(BaseModel):
    """One other visible entity; ``kind`` extends to monster/drop in W2."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    kind: Literal["player"] = "player"
    id: int
    name: str
    x: int
    y: int


class SnapshotMsg(BaseModel):
    """S2C: periodic vision snapshot; ``seq`` is monotonic per connection (design 3.4/8.4)."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    t: Literal["snapshot"] = "snapshot"
    seq: int
    char: CharacterState
    entities: list[EntityState] = Field(default_factory=list)


class ItemState(BaseModel):
    """One inventory/equipped item (content lands in W2; the shape is fixed now)."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    code: str
    slot: int
    quantity: int = 1
    durability: int | None = None


class SkillState(BaseModel):
    """One skill proficiency entry (design 4.4)."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    code: str
    rank: int = 0
    exp: int = 0


class QuestStateView(BaseModel):
    """One quest progress entry (design 5.7)."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    id: str
    status: str
    progress: int = 0


class InventoryState(BaseModel):
    """Inventory block shared by char_ready and the inventory delta message."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    items: list[ItemState] = Field(default_factory=list)
    gold: int = 0


class SkillsState(BaseModel):
    """Skill list block shared by char_ready and the skills delta message."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    skills: list[SkillState] = Field(default_factory=list)


class QuestsState(BaseModel):
    """Quest list block shared by char_ready and the quest_state message."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    quests: list[QuestStateView] = Field(default_factory=list)


class HelloMsg(BaseModel):
    """S2C: first frame after connect, before auth."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    t: Literal["hello"] = "hello"
    server_time: int
    tick_ms: int


class CharReadyMsg(BaseModel):
    """S2C: select_char accepted; full state plus the initial vision (design 3.6)."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    t: Literal["char_ready"] = "char_ready"
    character: CharacterState
    vision: SnapshotMsg
    inventory: InventoryState
    skills: SkillsState
    quest_state: QuestsState


class EventMsg(BaseModel):
    """S2C: immediate event; kind is kill/levelup/death/drop_appear/.../chat/system."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    t: Literal["event"] = "event"
    kind: str
    data: JsonValue = None


class InventoryMsg(BaseModel):
    """S2C: inventory delta, pushed immediately after pickup/trade (design 3.6)."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    t: Literal["inventory"] = "inventory"
    items: list[ItemState] = Field(default_factory=list)
    gold: int


class SkillsMsg(BaseModel):
    """S2C: skill proficiency delta (design 3.6)."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    t: Literal["skills"] = "skills"
    skills: list[SkillState] = Field(default_factory=list)


class NpcDialogMsg(BaseModel):
    """S2C: data for an opened NPC dialog (design 3.6)."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    t: Literal["npc_dialog"] = "npc_dialog"
    npc_id: str
    kind: str
    shop_items: list[ItemState] | None = None
    teleport_list: list[str] | None = None
    repair_cost: int | None = None
    quests: list[QuestStateView] | None = None


class QuestStateMsg(BaseModel):
    """S2C: full quest progress sync (design 3.6)."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    t: Literal["quest_state"] = "quest_state"
    quests: list[QuestStateView] = Field(default_factory=list)


class PongMsg(BaseModel):
    """S2C: heartbeat ack."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    t: Literal["pong"] = "pong"


class KickMsg(BaseModel):
    """S2C: forced disconnect (duplicate login, ban, ...)."""

    model_config: ClassVar[ConfigDict] = _S2C_CONFIG
    t: Literal["kick"] = "kick"
    reason: str


Outbound = (
    HelloMsg
    | CharReadyMsg
    | SnapshotMsg
    | EventMsg
    | InventoryMsg
    | SkillsMsg
    | NpcDialogMsg
    | QuestStateMsg
    | PongMsg
    | KickMsg
)


_C2S_BY_TYPE: Final[dict[str, type[Inbound]]] = {
    "auth": AuthMsg,
    "select_char": SelectCharMsg,
    "move": MoveMsg,
    "chat": ChatMsg,
    "logout_char": LogoutCharMsg,
    "cmd": CmdMsg,
    "npc": NpcMsg,
    "ping": PingMsg,
}


def encode_message(message: Inbound | Outbound) -> str:
    """Serialize one protocol frame (``t`` discriminator plus payload)."""
    return message.model_dump_json(by_alias=True, exclude_none=True)


def parse_message(raw: str) -> Inbound | None:
    """Parse one inbound frame; None for malformed JSON, unknown ``t``, or invalid payloads."""
    try:
        payload: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    message_type: object = payload.get("t")
    if not isinstance(message_type, str):
        return None
    model = _C2S_BY_TYPE.get(message_type)
    if model is None:
        return None
    try:
        return model.model_validate(payload)
    except ValidationError:
        return None
