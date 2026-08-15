import { afterEach, describe, expect, it } from 'vitest';

import { decodeInbound, encodeC2S } from './codec';
import type { CharacterState, SnapshotMsg } from './protocol';
import { defaultWsUrl, WsClient } from './wsClient';
import type { WsSocketLike } from './wsClient';

interface FakeSocket extends WsSocketLike {
  readyState: number;
  sent: string[];
  closed: { code?: number; reason?: string }[];
}

function makeFakeSocket(): FakeSocket {
  const fake: FakeSocket = {
    readyState: 0,
    onopen: null,
    onclose: null,
    onmessage: null,
    onerror: null,
    sent: [],
    closed: [],
    send(data: string) {
      fake.sent.push(data);
    },
    close(code?: number, reason?: string) {
      fake.closed.push({ code, reason });
    },
  };
  return fake;
}

interface FakeTimers {
  scheduler: { setTimeout: (cb: () => void, ms: number) => number; clearTimeout: (h: number) => void };
  pending: () => [number, number][];
  run: (elapsedMs: number) => void;
}

function makeFakeTimers(): FakeTimers {
  let nextHandle = 1;
  const timers = new Map<number, { callback: () => void; delayMs: number }>();
  return {
    scheduler: {
      setTimeout(callback: () => void, delayMs: number): number {
        const handle = nextHandle++;
        timers.set(handle, { callback, delayMs });
        return handle;
      },
      clearTimeout(handle: number): void {
        timers.delete(handle);
      },
    },
    pending(): [number, number][] {
      return [...timers.entries()]
        .map(([handle, timer]) => [handle, timer.delayMs] as [number, number])
        .sort((a, b) => a[0] - b[0]);
    },
    run(elapsedMs: number): void {
      for (const [handle, timer] of [...timers.entries()]) {
        if (timer.delayMs <= elapsedMs) {
          timers.delete(handle);
          timer.callback();
        }
      }
    },
  };
}

const CHAR: CharacterState = {
  id: 1,
  name: 'Hero',
  class: 'warrior',
  level: 30,
  exp: 100,
  hp: 200,
  mp: 50,
  gold: 5,
  bound_city: 'bicheng',
  map_id: 'bicheng',
  x: 0,
  y: 0,
};

function makeSnapshot(seq: number, x = 0, y = 0): SnapshotMsg {
  return { t: 'snapshot', seq, char: { ...CHAR, x, y }, entities: [] };
}

function makeMessageEvent(data: string): MessageEvent {
  return new MessageEvent('message', { data });
}

describe('codec', () => {
  it('encodes C2S frames with the t discriminator', () => {
    expect(encodeC2S({ t: 'auth', ws_token: 'tok-12345678', device: '' })).toBe(
      '{"t":"auth","ws_token":"tok-12345678","device":""}',
    );
    expect(encodeC2S({ t: 'select_char', char_id: 7 })).toBe('{"t":"select_char","char_id":7}');
  });

  it('round-trips a snapshot frame with entities', () => {
    const frame: SnapshotMsg = {
      t: 'snapshot',
      seq: 9,
      char: CHAR,
      entities: [{ kind: 'player', id: 2, name: 'Other', x: 3, y: 4 }],
    };
    expect(decodeInbound(JSON.stringify(frame))).toEqual(frame);
  });

  it('round-trips a full char_ready frame', () => {
    const frame = {
      t: 'char_ready',
      character: CHAR,
      vision: makeSnapshot(1),
      inventory: { items: [{ code: 'potion', slot: 0, quantity: 3, durability: 9 }], gold: 5 },
      skills: { skills: [{ code: 'cleave', rank: 5, exp: 10 }] },
      quest_state: { quests: [{ id: 'q1', status: 'active', progress: 1 }] },
    };
    expect(decodeInbound(JSON.stringify(frame))).toEqual(frame);
  });

  it('rejects malformed, unknown, and incomplete frames', () => {
    expect(decodeInbound('not json')).toBeUndefined();
    expect(decodeInbound('[1,2,3]')).toBeUndefined();
    expect(decodeInbound('{"t":"nope"}')).toBeUndefined();
    expect(decodeInbound('{"t":"snapshot","seq":1}')).toBeUndefined();
    expect(decodeInbound('{"t":"kick"}')).toBeUndefined();
    expect(decodeInbound('{"t":"hello","server_time":1}')).toBeUndefined();
  });
});

describe('defaultWsUrl', () => {
  const originalLocation = window.location;

  afterEach(() => {
    Object.defineProperty(window, 'location', { value: originalLocation, configurable: true });
  });

  it('derives ws(s)://host/ws from the page location', () => {
    Object.defineProperty(window, 'location', {
      value: { protocol: 'http:', host: '127.0.0.1:5173' },
      configurable: true,
    });
    expect(defaultWsUrl()).toBe('ws://127.0.0.1:5173/ws');

    Object.defineProperty(window, 'location', {
      value: { protocol: 'https:', host: 'game.example.com' },
      configurable: true,
    });
    expect(defaultWsUrl()).toBe('wss://game.example.com/ws');
  });
});

describe('WsClient', () => {
  it('reconnects with capped exponential backoff, resetting after a successful open', () => {
    const sockets: FakeSocket[] = [];
    const fakeTimers = makeFakeTimers();
    const client = new WsClient({
      url: 'ws://example.test/ws',
      socketFactory: () => {
        const socket = makeFakeSocket();
        sockets.push(socket);
        return socket;
      },
      scheduler: fakeTimers.scheduler,
    });

    client.connect();
    expect(sockets).toHaveLength(1);

    sockets[0].onclose?.(new CloseEvent('close', { code: 1006 }));
    expect(fakeTimers.pending()).toEqual([[1, 1000]]);
    fakeTimers.run(1000);
    expect(sockets).toHaveLength(2);

    sockets[1].onclose?.(new CloseEvent('close', { code: 1006 }));
    expect(fakeTimers.pending()).toEqual([[2, 2000]]);
    fakeTimers.run(2000);
    expect(sockets).toHaveLength(3);

    sockets[2].onclose?.(new CloseEvent('close', { code: 1006 }));
    expect(fakeTimers.pending()).toEqual([[3, 4000]]);
    fakeTimers.run(4000);
    expect(sockets).toHaveLength(4);

    sockets[3].onclose?.(new CloseEvent('close', { code: 1006 }));
    expect(fakeTimers.pending()).toEqual([[4, 8000]]);
    fakeTimers.run(8000);
    expect(sockets).toHaveLength(5);

    sockets[4].onclose?.(new CloseEvent('close', { code: 1006 }));
    expect(fakeTimers.pending()).toEqual([[5, 10000]]);
    fakeTimers.run(10000);
    expect(sockets).toHaveLength(6);

    // A successful open resets the backoff to the base delay.
    sockets[5].onopen?.(new MessageEvent('open'));
    sockets[5].onclose?.(new CloseEvent('close', { code: 1006 }));
    expect(fakeTimers.pending()).toEqual([[7, 1000]]);
  });

  it('reports the first successful open as initial and later reopens as reconnect', () => {
    const sockets: FakeSocket[] = [];
    const fakeTimers = makeFakeTimers();
    const client = new WsClient({
      url: 'ws://example.test/ws',
      socketFactory: () => {
        const socket = makeFakeSocket();
        sockets.push(socket);
        return socket;
      },
      scheduler: fakeTimers.scheduler,
    });

    const reasons: string[] = [];
    client.on('open', (message) => reasons.push(message.reason));
    client.connect();
    sockets[0].onopen?.(new MessageEvent('open'));
    sockets[0].onclose?.(new CloseEvent('close', { code: 1006 }));
    fakeTimers.run(1000);
    sockets[1].onopen?.(new MessageEvent('open'));
    expect(reasons).toEqual(['initial', 'reconnect']);

    client.disconnect();
  });

  it('sends a ping every heartbeat after open and stops after disconnect', () => {
    const socket = makeFakeSocket();
    const fakeTimers = makeFakeTimers();
    const client = new WsClient({
      url: 'ws://example.test/ws',
      heartbeatMs: 15_000,
      socketFactory: () => socket,
      scheduler: fakeTimers.scheduler,
    });

    const pongs: unknown[] = [];
    client.on('pong', () => pongs.push(true));
    client.connect();
    socket.readyState = 1;
    socket.onopen?.(new MessageEvent('open'));

    expect(socket.sent).toEqual([]);
    fakeTimers.run(15_000);
    expect(socket.sent).toEqual([JSON.stringify({ t: 'ping' })]);
    fakeTimers.run(30_000);
    expect(socket.sent).toEqual([JSON.stringify({ t: 'ping' }), JSON.stringify({ t: 'ping' })]);

    socket.onmessage?.(makeMessageEvent('{"t":"pong"}'));
    expect(pongs).toEqual([true]);

    client.disconnect();
    expect(socket.closed).toEqual([{ code: 1000, reason: 'client disconnect' }]);
    fakeTimers.run(45_000);
    expect(socket.sent).toHaveLength(2);
  });

  it('does not reconnect after a clean user disconnect', () => {
    const sockets: FakeSocket[] = [];
    const fakeTimers = makeFakeTimers();
    const client = new WsClient({
      url: 'ws://example.test/ws',
      socketFactory: () => {
        const socket = makeFakeSocket();
        sockets.push(socket);
        return socket;
      },
      scheduler: fakeTimers.scheduler,
    });

    client.connect();
    sockets[0].readyState = 1;
    sockets[0].onopen?.(new MessageEvent('open'));
    client.disconnect();
    expect(fakeTimers.pending()).toEqual([]);
    expect(sockets).toHaveLength(1);
    fakeTimers.run(60_000);
    expect(sockets).toHaveLength(1);
  });

  it('dispatches typed events for decoded frames and reports unparseable ones as errors', () => {
    const socket = makeFakeSocket();
    const client = new WsClient({
      url: 'ws://example.test/ws',
      socketFactory: () => socket,
      scheduler: makeFakeTimers().scheduler,
    });
    client.connect();

    const snapshots: SnapshotMsg[] = [];
    const pongs: number[] = [];
    const errors: string[] = [];
    const unsubscribe = [
      client.on('snapshot', (message) => snapshots.push(message)),
      client.on('pong', () => pongs.push(1)),
      client.on('error', (message) => errors.push(message.message)),
    ];

    const snapshotFrame = makeSnapshot(1, 3, 4);
    socket.onmessage?.(makeMessageEvent(JSON.stringify(snapshotFrame)));
    socket.onmessage?.(makeMessageEvent('{"t":"pong"}'));
    socket.onmessage?.(makeMessageEvent('not-json'));
    socket.onmessage?.(makeMessageEvent('{"t":"unknown_type"}'));

    expect(snapshots).toEqual([snapshotFrame]);
    expect(pongs).toEqual([1]);
    expect(errors).toHaveLength(2);

    for (const stop of unsubscribe) stop();
    socket.onmessage?.(makeMessageEvent('{"t":"pong"}'));
    expect(pongs).toHaveLength(1);
  });

  it('serializes C2S send helpers with the t discriminator', () => {
    const socket = makeFakeSocket();
    const client = new WsClient({
      url: 'ws://example.test/ws',
      socketFactory: () => socket,
      scheduler: makeFakeTimers().scheduler,
    });
    client.connect();
    socket.readyState = 1;
    socket.onopen?.(new MessageEvent('open'));

    client.auth('tok-12345678', 'mobile');
    client.selectChar(42);
    client.move(120, 30);
    client.chat('hello');
    client.cmd('drink', { potion: 'small' });
    client.cmd('auto_battle');
    client.npc('npc_store', 'buy', { item: 'sword' });
    client.ping();
    client.logoutChar();

    expect(socket.sent).toEqual([
      '{"t":"auth","ws_token":"tok-12345678","device":"mobile"}',
      '{"t":"select_char","char_id":42}',
      '{"t":"move","x":120,"y":30}',
      '{"t":"chat","text":"hello"}',
      '{"t":"cmd","action":"drink","args":{"potion":"small"}}',
      '{"t":"cmd","action":"auto_battle"}',
      '{"t":"npc","npc_id":"npc_store","action":"buy","args":{"item":"sword"}}',
      '{"t":"ping"}',
      '{"t":"logout_char"}',
    ]);
  });

  it('send returns false while the socket is not open', () => {
    const socket = makeFakeSocket();
    const client = new WsClient({
      url: 'ws://example.test/ws',
      socketFactory: () => socket,
      scheduler: makeFakeTimers().scheduler,
    });
    client.connect();
    expect(client.ping()).toBe(false);
    expect(client.isOpen).toBe(false);
    expect(socket.sent).toEqual([]);
  });
});
