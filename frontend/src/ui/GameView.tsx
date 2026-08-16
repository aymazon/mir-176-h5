// Game session: owns WsClient + ViewStore + the PIXI World. Handles the
// enter flow (ws_token → auth → select_char), reconnects, kick teardown,
// click-to-move, the virtual d-pad, and the e2e debug hook.

import { useCallback, useEffect, useRef, useState, type PointerEvent, type MouseEvent } from 'react';

import { ApiError, fetchWsToken, type CharacterOut } from '../api/rest';
import { World } from '../game/world';
import { cellFromScreenDelta } from '../game/mapLayer';
import type { SpriteDirection } from '../game/assets';
import { WsClient, type WsEventMap } from '../net/wsClient';
import { ViewStore } from '../store/viewStore';
import type { CharacterState } from '../net/protocol';

import HUD from './HUD';

export interface Mir176DebugHook {
  readonly move: (x: number, y: number) => boolean;
  readonly char: CharacterState | undefined;
  readonly seq: number | null;
}

declare global {
  interface Window {
    __mir176?: Mir176DebugHook;
  }
}

const DPAD_STEP_MS = 200;

interface GameViewProps {
  readonly character: CharacterOut;
  readonly onExit: (reason?: string) => void;
}

interface DPadButtonProps {
  readonly direction: 'up' | 'down' | 'left' | 'right';
  readonly delta: [number, number];
  readonly onHold: (dx: number, dy: number) => void;
  readonly onRelease: () => void;
}

function DPadButton({ direction, delta, onHold, onRelease }: DPadButtonProps) {
  const [dx, dy] = delta;
  return (
    <button
      type="button"
      className={`dpad__btn dpad__btn--${direction}`}
      aria-label={`向${direction}移动`}
      onPointerDown={(event: PointerEvent<HTMLButtonElement>) => {
        event.preventDefault();
        onHold(dx, dy);
      }}
      onPointerUp={onRelease}
      onPointerLeave={onRelease}
      onPointerCancel={onRelease}
      onContextMenu={(event) => event.preventDefault()}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M4 10 L8 6 L12 10" />
      </svg>
    </button>
  );
}

export default function GameView({ character, onExit }: GameViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewStoreRef = useRef<ViewStore | null>(null);
  if (viewStoreRef.current === null) viewStoreRef.current = new ViewStore();
  const viewStore = viewStoreRef.current;

  const clientRef = useRef<WsClient | null>(null);
  const worldRef = useRef<World | null>(null);
  const holdTimerRef = useRef<number | null>(null);
  const onExitRef = useRef(onExit);
  useEffect(() => {
    onExitRef.current = onExit;
  });

  const [banner, setBanner] = useState<string | null>('进入比奇城…');

  const stopHold = useCallback((): void => {
    if (holdTimerRef.current !== null) {
      window.clearInterval(holdTimerRef.current);
      holdTimerRef.current = null;
    }
  }, []);

  const startHold = useCallback(
    (dx: number, dy: number): void => {
      if (holdTimerRef.current !== null) return;
      const step = (): void => {
        // Adjacent cell from the CURRENT interpolated int cell, per D15.
        const position = viewStore.characterPositionAt(performance.now());
        if (position !== undefined) {
          clientRef.current?.move(Math.round(position.x) + dx, Math.round(position.y) + dy);
        }
      };
      step();
      holdTimerRef.current = window.setInterval(step, DPAD_STEP_MS);
    },
    [viewStore],
  );

  useEffect(() => stopHold, [stopHold]);

  const handleWorldClick = (event: MouseEvent<HTMLDivElement>): void => {
    const state = viewStore.character;
    if (state === undefined) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const target = cellFromScreenDelta(
      state.x,
      state.y,
      event.clientX - (rect.left + rect.width / 2),
      event.clientY - (rect.top + rect.height / 2),
    );
    const facing: SpriteDirection =
      Math.abs(target.x - state.x) >= Math.abs(target.y - state.y)
        ? target.x >= state.x
          ? 'right'
          : 'left'
        : target.y >= state.y
          ? 'down'
          : 'up';
    worldRef.current?.setSelfDirection(facing);
    clientRef.current?.move(target.x, target.y);
  };

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) return;
    let disposed = false;
    let kicked = false;

    const client = new WsClient();
    clientRef.current = client;
    const world = new World({ container, viewStore });
    worldRef.current = world;

    const enter = async (): Promise<void> => {
      // Every (re)open needs a fresh one-time ws_token.
      try {
        const { ws_token } = await fetchWsToken();
        if (disposed) return;
        client.auth(ws_token, 'h5');
        client.selectChar(character.id);
      } catch (err) {
        if (!disposed) {
          setBanner(err instanceof ApiError ? `连接失败：${err.message}` : '连接失败，请检查网络');
        }
      }
    };

    const unsubOpen = client.on('open', (event: WsEventMap['open']) => {
      if (event.reason === 'reconnect') viewStore.reset();
      void enter();
    });
    const unsubCharReady = client.on('char_ready', (message) => {
      viewStore.applyCharReady(message, performance.now());
      world.setSelfCharacter(message.character.class);
      if (!disposed) setBanner(null);
    });
    const unsubSnapshot = client.on('snapshot', (message) => {
      viewStore.applySnapshot(message, performance.now());
    });
    const unsubKick = client.on('kick', (message) => {
      if (kicked) return;
      kicked = true;
      client.disconnect();
      onExitRef.current(message.reason);
    });
    const unsubClose = client.on('close', () => {
      if (!kicked && !disposed) setBanner('连接断开，重连中…');
    });

    client.connect();
    void world.start().catch(() => {
      if (!disposed) setBanner('渲染初始化失败');
    });

    window.__mir176 = {
      move: (x, y) => client.move(x, y),
      get char() {
        return viewStore.character;
      },
      get seq() {
        return viewStore.lastAppliedSeq;
      },
    };

    return () => {
      disposed = true;
      unsubOpen();
      unsubCharReady();
      unsubSnapshot();
      unsubKick();
      unsubClose();
      stopHold();
      if (!kicked && client.isOpen) client.logoutChar();
      client.disconnect();
      world.stop();
      clientRef.current = null;
      worldRef.current = null;
      if (window.__mir176 !== undefined) delete window.__mir176;
    };
  }, []);

  return (
    <div className="game-view">
      <div className="game-view__play" ref={containerRef} onClick={handleWorldClick} />
      <HUD viewStore={viewStore} />
      <div className="dpad" role="group" aria-label="移动方向键" onContextMenu={(event) => event.preventDefault()}>
        <DPadButton direction="up" delta={[0, -1]} onHold={startHold} onRelease={stopHold} />
        <DPadButton direction="left" delta={[-1, 0]} onHold={startHold} onRelease={stopHold} />
        <DPadButton direction="down" delta={[0, 1]} onHold={startHold} onRelease={stopHold} />
        <DPadButton direction="right" delta={[1, 0]} onHold={startHold} onRelease={stopHold} />
      </div>
      {banner !== null && (
        <div className="game-view__banner" role="status">
          {banner}
        </div>
      )}
    </div>
  );
}
