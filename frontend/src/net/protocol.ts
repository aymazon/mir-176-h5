// Wire protocol types for the Mir 1.76 H5 WebSocket gateway.
// Field names must match backend/app/net/protocol.py one-to-one (source of truth).

/** Opaque client payload (cmd/npc args, event data): any JSON value. */
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { readonly [key: string]: JsonValue };

/** Full character state. Serialized as `class` on the wire (not `character_class`). */
export interface CharacterState {
  readonly id: number;
  readonly name: string;
  readonly class: string;
  readonly level: number;
  readonly exp: number;
  readonly hp: number;
  readonly mp: number;
  readonly gold: number;
  readonly bound_city: string;
  readonly map_id: string;
  readonly x: number;
  readonly y: number;
}

/** One other visible entity; `kind` extends to monster/drop in W2. */
export interface EntityState {
  readonly kind: 'player';
  readonly id: number;
  readonly name: string;
  readonly x: number;
  readonly y: number;
}

/** One inventory/equipped item (content lands in W2; the shape is fixed now). */
export interface ItemState {
  readonly code: string;
  readonly slot: number;
  readonly quantity: number;
  readonly durability?: number;
}

/** One skill proficiency entry (design 4.4). */
export interface SkillState {
  readonly code: string;
  readonly rank: number;
  readonly exp: number;
}

/** One quest progress entry (design 5.7). */
export interface QuestStateView {
  readonly id: string;
  readonly status: string;
  readonly progress: number;
}

// ---------------------------------------------------------------------------
// Client -> server frames (C2S). Serialized by the client with the `t` tag.
// ---------------------------------------------------------------------------

/** First frame after connect; one-time ws_token plus a device tag. */
export interface AuthMsg {
  readonly t: 'auth';
  readonly ws_token: string;
  readonly device: string;
}

/** Enter the game with one of the account's characters (one per connection). */
export interface SelectCharMsg {
  readonly t: 'select_char';
  readonly char_id: number;
}

/** Click-to-move intent; the server validates reachability and may reject. */
export interface MoveMsg {
  readonly t: 'move';
  readonly x: number;
  readonly y: number;
}

/** Global chat, at most 100 characters. */
export interface ChatMsg {
  readonly t: 'chat';
  readonly text: string;
}

/** Leave the world; the character is saved and the connection closes. */
export interface LogoutCharMsg {
  readonly t: 'logout_char';
}

/** Manual fallback commands (drinks, scrolls, auto-battle config; W2/W3). */
export interface CmdMsg {
  readonly t: 'cmd';
  readonly action: string;
  readonly args?: JsonValue;
}

/** NPC dialog actions (buy, sell, repair, teleport, quest; W3). */
export interface NpcMsg {
  readonly t: 'npc';
  readonly npc_id: string;
  readonly action: string;
  readonly args?: JsonValue;
}

/** Client heartbeat (client cadence is 15 s per design 3.4). */
export interface PingMsg {
  readonly t: 'ping';
}

export type C2SMessage =
  | AuthMsg
  | SelectCharMsg
  | MoveMsg
  | ChatMsg
  | LogoutCharMsg
  | CmdMsg
  | NpcMsg
  | PingMsg;

// ---------------------------------------------------------------------------
// Server -> client frames (S2C). Parsed by the client from inbound JSON.
// ---------------------------------------------------------------------------

/** Periodic vision snapshot; `seq` is monotonic per connection (design 3.4/8.4). */
export interface SnapshotMsg {
  readonly t: 'snapshot';
  readonly seq: number;
  readonly char: CharacterState;
  readonly entities: EntityState[];
}

/** First frame after connect, before auth. */
export interface HelloMsg {
  readonly t: 'hello';
  readonly server_time: number;
  readonly tick_ms: number;
}

/** select_char accepted; full state plus the initial vision (design 3.6). */
export interface CharReadyMsg {
  readonly t: 'char_ready';
  readonly character: CharacterState;
  readonly vision: SnapshotMsg;
  readonly inventory: { readonly items: ItemState[]; readonly gold: number };
  readonly skills: { readonly skills: SkillState[] };
  readonly quest_state: { readonly quests: QuestStateView[] };
}

/** Immediate event; kind is kill/levelup/death/drop_appear/.../chat/system. */
export interface EventMsg {
  readonly t: 'event';
  readonly kind: string;
  readonly data?: JsonValue;
}

/** Inventory delta, pushed immediately after pickup/trade (design 3.6). */
export interface InventoryMsg {
  readonly t: 'inventory';
  readonly items: ItemState[];
  readonly gold: number;
}

/** Skill proficiency delta (design 3.6). */
export interface SkillsMsg {
  readonly t: 'skills';
  readonly skills: SkillState[];
}

/** Data for an opened NPC dialog (design 3.6). */
export interface NpcDialogMsg {
  readonly t: 'npc_dialog';
  readonly npc_id: string;
  readonly kind: string;
  readonly shop_items?: ItemState[];
  readonly teleport_list?: string[];
  readonly repair_cost?: number;
  readonly quests?: QuestStateView[];
}

/** Full quest progress sync (design 3.6). */
export interface QuestStateMsg {
  readonly t: 'quest_state';
  readonly quests: QuestStateView[];
}

/** Heartbeat ack. */
export interface PongMsg {
  readonly t: 'pong';
}

/** Forced disconnect (duplicate login, ban, ...). */
export interface KickMsg {
  readonly t: 'kick';
  readonly reason: string;
}

export type S2CMessage =
  | HelloMsg
  | CharReadyMsg
  | SnapshotMsg
  | EventMsg
  | InventoryMsg
  | SkillsMsg
  | NpcDialogMsg
  | QuestStateMsg
  | PongMsg
  | KickMsg;
