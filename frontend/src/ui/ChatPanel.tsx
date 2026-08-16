// Global chat window (D14): the last 50 broadcast chat lines plus the send
// input. The wrapper is pointer-transparent; only the log, the input, and the
// send button capture pointers, so the panel never blocks world clicks or
// the d-pad.

import { useEffect, useRef, useState, type FormEvent } from 'react';

import type { JsonValue } from '../net/protocol';
import type { WsClient } from '../net/wsClient';

const MAX_LINES = 50;
const MAX_TEXT_LENGTH = 100;

interface ChatPanelProps {
  readonly client: WsClient;
}

function chatLine(data: JsonValue | undefined): string | null {
  if (typeof data !== 'object' || data === null || Array.isArray(data)) return null;
  const record = data as Record<string, JsonValue>;
  const name = record['name'];
  const text = record['text'];
  if (typeof name !== 'string' || typeof text !== 'string') return null;
  return `${name}: ${text}`;
}

export default function ChatPanel({ client }: ChatPanelProps) {
  const [lines, setLines] = useState<string[]>([]);
  const [draft, setDraft] = useState('');
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    return client.on('event', (message) => {
      if (message.kind !== 'chat') return;
      const line = chatLine(message.data);
      if (line === null) return;
      setLines((prev) => [...prev, line].slice(-MAX_LINES));
    });
  }, [client]);

  useEffect(() => {
    const log = logRef.current;
    if (log !== null) log.scrollTop = log.scrollHeight;
  }, [lines]);

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const text = draft.trim();
    if (text.length === 0) return;
    if (client.chat(text)) setDraft('');
  };

  return (
    <div className="chat">
      <div className="chat__log" ref={logRef} aria-live="polite">
        {lines.length === 0 ? (
          <p className="chat__empty">暂无消息</p>
        ) : (
          lines.map((line, index) => (
            <p className="chat__line" key={`${index}-${line}`}>
              {line}
            </p>
          ))
        )}
      </div>
      <form className="chat__form" onSubmit={submit}>
        <input
          className="chat__input"
          aria-label="聊天"
          maxLength={MAX_TEXT_LENGTH}
          placeholder="说点什么…"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" className="btn btn--primary chat__send">
          发送
        </button>
      </form>
    </div>
  );
}
