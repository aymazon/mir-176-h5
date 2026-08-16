// Typed REST client for the Mir 1.76 H5 backend (all paths proxied by vite /api).

export type CharacterClassKind = 'warrior' | 'mage' | 'taoist';

export interface CharacterOut {
  readonly id: number;
  readonly name: string;
  readonly class: CharacterClassKind;
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

export interface RegisterResponse {
  readonly id: number;
  readonly username: string;
}

export interface LoginResponse {
  readonly token: string;
  readonly token_type: string;
}

export interface WsTokenResponse {
  readonly ws_token: string;
  readonly expires_in: number;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

const JWT_STORAGE_KEY = 'mir176_jwt';

export function getJwt(): string | null {
  return window.localStorage.getItem(JWT_STORAGE_KEY);
}

export function setJwt(token: string): void {
  window.localStorage.setItem(JWT_STORAGE_KEY, token);
}

export function clearJwt(): void {
  window.localStorage.removeItem(JWT_STORAGE_KEY);
}

interface RequestOptions {
  readonly method?: string;
  readonly body?: unknown;
  readonly auth?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (options.auth !== false) {
    const jwt = getJwt();
    if (jwt !== null) headers.Authorization = `Bearer ${jwt}`;
  }
  const response = await fetch(path, {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (response.status === 204) return undefined as T;
  const payload: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    let detail = response.statusText;
    if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
      const candidate = (payload as { detail: unknown }).detail;
      if (typeof candidate === 'string') detail = candidate;
    }
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

export function register(username: string, password: string): Promise<RegisterResponse> {
  return request<RegisterResponse>('/api/auth/register', {
    method: 'POST',
    body: { username, password },
    auth: false,
  });
}

export function login(username: string, password: string): Promise<LoginResponse> {
  return request<LoginResponse>('/api/auth/login', {
    method: 'POST',
    body: { username, password },
    auth: false,
  });
}

export function fetchWsToken(): Promise<WsTokenResponse> {
  return request<WsTokenResponse>('/api/auth/ws_token', { method: 'POST' });
}

export function fetchCharacters(): Promise<CharacterOut[]> {
  return request<CharacterOut[]>('/api/characters');
}

export function createCharacter(
  name: string,
  characterClass: CharacterClassKind,
): Promise<CharacterOut> {
  return request<CharacterOut>('/api/characters', {
    method: 'POST',
    body: { name, class: characterClass },
  });
}

export function deleteCharacter(id: number, name: string): Promise<void> {
  return request<void>(`/api/characters/${id}`, { method: 'DELETE', body: { name } });
}
