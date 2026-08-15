// JSON codec for the WS gateway protocol: serializes C2S frames and parses
// S2C frames at the wire boundary (unknown/invalid frames yield undefined).

import type {
  C2SMessage,
  CharacterState,
  CharReadyMsg,
  EntityState,
  EventMsg,
  HelloMsg,
  InventoryMsg,
  ItemState,
  KickMsg,
  NpcDialogMsg,
  PongMsg,
  QuestStateMsg,
  QuestStateView,
  S2CMessage,
  SkillState,
  SkillsMsg,
  SnapshotMsg,
} from './protocol';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function guardNumber(record: Record<string, unknown>, key: string): boolean {
  const value = record[key];
  return typeof value === 'number' && Number.isFinite(value);
}

function guardCharacterState(value: unknown): value is CharacterState {
  if (!isRecord(value)) return false;
  return (
    guardNumber(value, 'id') &&
    typeof value.name === 'string' &&
    typeof value.class === 'string' &&
    guardNumber(value, 'level') &&
    guardNumber(value, 'exp') &&
    guardNumber(value, 'hp') &&
    guardNumber(value, 'mp') &&
    guardNumber(value, 'gold') &&
    typeof value.bound_city === 'string' &&
    typeof value.map_id === 'string' &&
    guardNumber(value, 'x') &&
    guardNumber(value, 'y')
  );
}

function guardEntity(value: unknown): value is EntityState {
  if (!isRecord(value)) return false;
  return (
    value.kind === 'player' &&
    guardNumber(value, 'id') &&
    typeof value.name === 'string' &&
    guardNumber(value, 'x') &&
    guardNumber(value, 'y')
  );
}

function guardItem(value: unknown): value is ItemState {
  if (!isRecord(value)) return false;
  return (
    typeof value.code === 'string' &&
    guardNumber(value, 'slot') &&
    guardNumber(value, 'quantity') &&
    (value.durability === undefined || typeof value.durability === 'number')
  );
}

function guardSkill(value: unknown): value is SkillState {
  if (!isRecord(value)) return false;
  return (
    typeof value.code === 'string' && guardNumber(value, 'rank') && guardNumber(value, 'exp')
  );
}

function guardQuest(value: unknown): value is QuestStateView {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === 'string' &&
    typeof value.status === 'string' &&
    guardNumber(value, 'progress')
  );
}

function guardEntityArray(value: unknown): value is EntityState[] {
  return Array.isArray(value) && value.every(guardEntity);
}

function guardItemArray(value: unknown): value is ItemState[] {
  return Array.isArray(value) && value.every(guardItem);
}

function guardSkillArray(value: unknown): value is SkillState[] {
  return Array.isArray(value) && value.every(guardSkill);
}

function guardQuestArray(value: unknown): value is QuestStateView[] {
  return Array.isArray(value) && value.every(guardQuest);
}

function guardSnapshot(value: unknown): value is SnapshotMsg {
  if (!isRecord(value) || value.t !== 'snapshot' || !guardNumber(value, 'seq')) return false;
  return guardCharacterState(value.char) && guardEntityArray(value.entities);
}

function guardHello(value: unknown): value is HelloMsg {
  if (!isRecord(value) || value.t !== 'hello') return false;
  return guardNumber(value, 'server_time') && guardNumber(value, 'tick_ms');
}

function guardCharReady(value: unknown): value is CharReadyMsg {
  if (!isRecord(value) || value.t !== 'char_ready') return false;
  const inventory = value.inventory;
  const skills = value.skills;
  const questState = value.quest_state;
  return (
    guardCharacterState(value.character) &&
    guardSnapshot(value.vision) &&
    isRecord(inventory) &&
    guardItemArray(inventory.items) &&
    guardNumber(inventory, 'gold') &&
    isRecord(skills) &&
    guardSkillArray(skills.skills) &&
    isRecord(questState) &&
    guardQuestArray(questState.quests)
  );
}

function guardEvent(value: unknown): value is EventMsg {
  if (!isRecord(value) || value.t !== 'event') return false;
  return typeof value.kind === 'string';
}

function guardInventory(value: unknown): value is InventoryMsg {
  if (!isRecord(value) || value.t !== 'inventory') return false;
  return guardItemArray(value.items) && guardNumber(value, 'gold');
}

function guardSkills(value: unknown): value is SkillsMsg {
  if (!isRecord(value) || value.t !== 'skills') return false;
  return guardSkillArray(value.skills);
}

function guardNpcDialog(value: unknown): value is NpcDialogMsg {
  if (!isRecord(value) || value.t !== 'npc_dialog') return false;
  return (
    typeof value.npc_id === 'string' &&
    typeof value.kind === 'string' &&
    (value.shop_items === undefined || guardItemArray(value.shop_items)) &&
    (value.teleport_list === undefined || isStringArray(value.teleport_list)) &&
    (value.repair_cost === undefined || typeof value.repair_cost === 'number') &&
    (value.quests === undefined || guardQuestArray(value.quests))
  );
}

function guardQuestState(value: unknown): value is QuestStateMsg {
  if (!isRecord(value) || value.t !== 'quest_state') return false;
  return guardQuestArray(value.quests);
}

function guardPong(value: unknown): value is PongMsg {
  return isRecord(value) && value.t === 'pong';
}

function guardKick(value: unknown): value is KickMsg {
  if (!isRecord(value) || value.t !== 'kick') return false;
  return typeof value.reason === 'string';
}

export function decodeInbound(raw: string): S2CMessage | undefined {
  let payload: unknown;
  try {
    payload = JSON.parse(raw);
  } catch {
    return undefined;
  }
  if (!isRecord(payload)) return undefined;
  if (guardHello(payload)) return payload;
  if (guardCharReady(payload)) return payload;
  if (guardSnapshot(payload)) return payload;
  if (guardEvent(payload)) return payload;
  if (guardInventory(payload)) return payload;
  if (guardSkills(payload)) return payload;
  if (guardNpcDialog(payload)) return payload;
  if (guardQuestState(payload)) return payload;
  if (guardPong(payload)) return payload;
  if (guardKick(payload)) return payload;
  return undefined;
}

export function encodeC2S(message: C2SMessage): string {
  return JSON.stringify(message);
}
