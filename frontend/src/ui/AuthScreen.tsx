// Register/login screen (登录/注册 tabs); stores the JWT on success.

import { useState, type FormEvent } from 'react';

import { ApiError, login, register, setJwt } from '../api/rest';

type AuthMode = 'login' | 'register';

interface AuthScreenProps {
  readonly onAuthenticated: () => void;
}

export default function AuthScreen({ onAuthenticated }: AuthScreenProps) {
  const [mode, setMode] = useState<AuthMode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === 'register') await register(username.trim(), password);
      const response = await login(username.trim(), password);
      setJwt(response.token);
      onAuthenticated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '网络错误，请稍后再试');
      setBusy(false);
    }
  };

  return (
    <main className="auth-screen">
      <h1 className="app-title">
        Mir <span className="app-title__version">1.76</span> H5
      </h1>
      <p className="app-subtitle">传奇 1.76 · 竖屏挂机</p>
      <div className="auth-panel">
        <div className="auth-tabs" role="tablist" aria-label="登录或注册">
          <button
            type="button"
            role="tab"
            className="auth-tabs__tab"
            aria-selected={mode === 'login'}
            onClick={() => {
              setMode('login');
              setError(null);
            }}
          >
            登录
          </button>
          <button
            type="button"
            role="tab"
            className="auth-tabs__tab"
            aria-selected={mode === 'register'}
            onClick={() => {
              setMode('register');
              setError(null);
            }}
          >
            注册
          </button>
        </div>
        <form onSubmit={(event) => void handleSubmit(event)}>
          <div className="form-row">
            <label className="field">
              用户名
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
              />
            </label>
            <label className="field">
              密码
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                required
              />
            </label>
          </div>
          {error !== null && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {mode === 'login' ? '登录' : '注册'}
          </button>
        </form>
      </div>
    </main>
  );
}
