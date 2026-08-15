// WebSocket client for the Mir 1.76 H5 gateway: typed event dispatch,
// auto-reconnect with capped exponential backoff, and a client-side heartbeat.

import { decodeInbound, encodeC2S } from './codec';
import type {
  C2SMessage,
  CharReadyMsg,
  EventMsg,
  HelloMsg,
  InventoryMsg,
  JsonValue,
  KickMsg,
  NpcDialogMsg,
  PongMsg,
  QuestStateMsg,
  SkillsMsg,
  SnapshotMsg,
} from './protocol';

/** WebSocket readyState === OPEN (avoids depending on the WebSocket global). */
const WS_OPEN = 1;

export const RECONNECT_BASE_MS = 1000;
export const RECONNECT_MAX_MS = 10_000;
export const DEFAULT_HEARTBEAT_MS = 15_000;

/**
 * Reconnect delay after `failedAttempts` consecutive failed connections:
 * 1s, 2s, 4s, 8s, 10s, 10s, ... (capped at RECONNECT_MAX_MS).
 */
export function reconnectDelayMs(failedAttempts: number): number {
  if (!Number.isInteger(failedAttempts) || failedAttempts < 0) {
    throw new RangeError(`failedAttempts must be a non-negative integer, got ${failedAttempts}`);
  }
  return Math.min(RECONNECT_BASE_MS * 2 ** failedAttempts, RECONNECT_MAX_MS);
}

/** Dev default: the same host as the page, through the vite `/ws` proxy. */
export function defaultWsUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${window.location.host}/ws`;
}

export interface WsEventMap {
  readonly hello: HelloMsg;
  readonly char_ready: CharReadyMsg;
  readonly snapshot: SnapshotMsg;
  readonly event: EventMsg;
  readonly inventory: InventoryMsg;
  readonly skills: SkillsMsg;
  readonly npc_dialog: NpcDialogMsg;
  readonly quest_state: QuestStateMsg;
  readonly pong: PongMsg;
  readonly kick: KickMsg;
  readonly open: { readonly t: 'open'; readonly reason: 'initial' | 'reconnect' };
  readonly close: { readonly t: 'close'; readonly code: number; readonly reason: string; readonly wasClean: boolean };
  readonly error: { readonly t: 'error'; readonly message: string };
}

export type WsEventName = keyof WsEventMap;

export type WsEventPayload = WsEventMap[WsEventName];

/** Minimal socket surface shared by the browser WebSocket and test doubles. */
export interface WsSocketLike {
  readonly readyState: number;
  onopen: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

/** Timer surface so tests can drive reconnect/heartbeat deterministically. */
export interface Scheduler {
  setTimeout(callback: () => void, delayMs: number): number;
  clearTimeout(handle: number): void;
}

const defaultScheduler: Scheduler = {
  setTimeout: (callback, delayMs) => setTimeout(callback, delayMs),
  clearTimeout: (handle) => clearTimeout(handle),
};

export interface WsClientOptions {
  readonly url?: string;
  readonly heartbeatMs?: number;
  readonly socketFactory?: (url: string) => WsSocketLike;
  readonly scheduler?: Scheduler;
}

type WsListener = (payload: WsEventPayload) => void;

export class WsClient {
  private readonly url: string;
  private readonly heartbeatMs: number;
  private readonly socketFactory: (url: string) => WsSocketLike;
  private readonly scheduler: Scheduler;
  private readonly listeners = new Map<WsEventName, Set<WsListener>>();
  private socket: WsSocketLike | null = null;
  private failedAttempts = 0;
  private reconnectTimer: number | null = null;
  private heartbeatTimer: number | null = null;
  private running = false;
  private everOpened = false;

  constructor(options: WsClientOptions = {}) {
    this.url = options.url ?? defaultWsUrl();
    this.heartbeatMs = options.heartbeatMs ?? DEFAULT_HEARTBEAT_MS;
    this.socketFactory = options.socketFactory ?? ((url: string) => new WebSocket(url));
    this.scheduler = options.scheduler ?? defaultScheduler;
  }

  get isOpen(): boolean {
    return this.socket !== null && this.socket.readyState === WS_OPEN;
  }

  /** Start (or resume) the connection lifecycle; idempotent. */
  connect(): void {
    if (this.running) return;
    this.running = true;
    this.openSocket();
  }

  /** Stop the connection lifecycle; no automatic reconnect after this. */
  disconnect(): void {
    if (!this.running) return;
    this.running = false;
    this.clearTimer(this.reconnectTimer);
    this.reconnectTimer = null;
    this.stopHeartbeat();
    if (this.socket !== null) {
      this.socket.close(1000, 'client disconnect');
    }
  }

  on<K extends WsEventName>(type: K, listener: (payload: WsEventMap[K]) => void): () => void {
    const bucket = this.ensureBucket(type);
    // Bucket invariant: emit() routes each payload only to its own type's
    // bucket, so the narrowing from the event union to WsEventMap[K] holds.
    const wrapped: WsListener = (payload) => listener(payload as WsEventMap[K]);
    bucket.add(wrapped);
    return () => {
      bucket.delete(wrapped);
    };
  }

  send(message: C2SMessage): boolean {
    const socket = this.socket;
    if (socket === null || socket.readyState !== WS_OPEN) return false;
    socket.send(encodeC2S(message));
    return true;
  }

  auth(wsToken: string, device = ''): boolean {
    return this.send({ t: 'auth', ws_token: wsToken, device });
  }

  selectChar(charId: number): boolean {
    return this.send({ t: 'select_char', char_id: charId });
  }

  move(x: number, y: number): boolean {
    return this.send({ t: 'move', x, y });
  }

  chat(text: string): boolean {
    return this.send({ t: 'chat', text });
  }

  logoutChar(): boolean {
    return this.send({ t: 'logout_char' });
  }

  cmd(action: string, args?: JsonValue): boolean {
    return args === undefined ? this.send({ t: 'cmd', action }) : this.send({ t: 'cmd', action, args });
  }

  npc(npcId: string, action: string, args?: JsonValue): boolean {
    return args === undefined
      ? this.send({ t: 'npc', npc_id: npcId, action })
      : this.send({ t: 'npc', npc_id: npcId, action, args });
  }

  ping(): boolean {
    return this.send({ t: 'ping' });
  }

  private openSocket(): void {
    const socket = this.socketFactory(this.url);
    this.socket = socket;
    socket.onopen = () => this.handleOpen();
    socket.onmessage = (event) => this.handleMessage(event.data);
    socket.onclose = (event) => this.handleClose(event);
    socket.onerror = () => this.emit('error', { t: 'error', message: 'websocket error' });
  }

  private handleOpen(): void {
    this.failedAttempts = 0;
    this.startHeartbeat();
    const reason = this.everOpened ? 'reconnect' : 'initial';
    this.everOpened = true;
    this.emit('open', { t: 'open', reason });
  }

  private handleClose(event: CloseEvent): void {
    this.stopHeartbeat();
    this.socket = null;
    this.emit('close', {
      t: 'close',
      code: event.code,
      reason: event.reason,
      wasClean: event.wasClean,
    });
    if (!this.running) return;
    const delay = reconnectDelayMs(this.failedAttempts);
    this.failedAttempts += 1;
    this.reconnectTimer = this.scheduler.setTimeout(() => this.openSocket(), delay);
  }

  private handleMessage(data: unknown): void {
    if (typeof data !== 'string') return;
    const message = decodeInbound(data);
    if (message === undefined) {
      this.emit('error', { t: 'error', message: `unparseable frame: ${data.slice(0, 64)}` });
      return;
    }
    this.emit(message.t, message);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    const tick = (): void => {
      this.ping();
      this.heartbeatTimer = this.scheduler.setTimeout(tick, this.heartbeatMs);
    };
    this.heartbeatTimer = this.scheduler.setTimeout(tick, this.heartbeatMs);
  }

  private stopHeartbeat(): void {
    this.clearTimer(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  private clearTimer(handle: number | null): void {
    if (handle !== null) this.scheduler.clearTimeout(handle);
  }

  private ensureBucket(type: WsEventName): Set<WsListener> {
    let bucket = this.listeners.get(type);
    if (bucket === undefined) {
      bucket = new Set();
      this.listeners.set(type, bucket);
    }
    return bucket;
  }

  private emit(type: WsEventName, payload: WsEventPayload): void {
    const bucket = this.listeners.get(type);
    if (bucket === undefined) return;
    for (const listener of [...bucket]) listener(payload);
  }
}
