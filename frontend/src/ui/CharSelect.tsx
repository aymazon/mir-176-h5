// Character select: list (name/class/level), create (name ≤16 + class),
// delete with typed-name confirm, and per-character 进入 to the game.

import { useCallback, useEffect, useState, type FormEvent } from 'react';

import {
  ApiError,
  createCharacter,
  deleteCharacter,
  fetchCharacters,
  type CharacterClassKind,
  type CharacterOut,
} from '../api/rest';

const CLASS_LABELS: Record<CharacterClassKind, string> = {
  warrior: '战士',
  mage: '法师',
  taoist: '道士',
};

const MAX_NAME_LENGTH = 16;

interface CharSelectProps {
  readonly notice?: string;
  readonly onEnter: (character: CharacterOut) => void;
}

export default function CharSelect({ notice, onEnter }: CharSelectProps) {
  const [characters, setCharacters] = useState<CharacterOut[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [newClass, setNewClass] = useState<CharacterClassKind>('warrior');
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<CharacterOut | null>(null);
  const [confirmName, setConfirmName] = useState('');

  const refresh = useCallback(async (): Promise<void> => {
    try {
      setCharacters(await fetchCharacters());
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '网络错误，请稍后再试');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleCreate = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const name = newName.trim();
    if (name.length === 0 || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await createCharacter(name, newClass);
      setNewName('');
      await refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '网络错误，请稍后再试');
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async (): Promise<void> => {
    const target = deleteTarget;
    if (target === null || confirmName !== target.name || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await deleteCharacter(target.id, target.name);
      setDeleteTarget(null);
      setConfirmName('');
      await refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '网络错误，请稍后再试');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="char-select">
      <h1 className="char-select__title">角色选择</h1>
      {notice !== undefined && <p className="char-select__notice">{notice}</p>}
      {loadError !== null && <p className="form-error">{loadError}</p>}
      {actionError !== null && (
        <p className="form-error" role="alert">
          {actionError}
        </p>
      )}
      <ul className="char-list">
        {characters === null && <li className="char-empty">加载中…</li>}
        {characters !== null && characters.length === 0 && (
          <li className="char-empty">还没有角色，创建一个吧</li>
        )}
        {characters !== null &&
          characters.map((character) => (
            <li className="char-card" key={character.id}>
              <div>
                <span className="char-card__name">{character.name}</span>
                <span className="char-card__meta">
                  {CLASS_LABELS[character.class]} · Lv.{character.level}
                </span>
              </div>
              <div className="char-card__actions">
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => onEnter(character)}
                >
                  进入
                </button>
                <button
                  type="button"
                  className="btn btn--danger"
                  onClick={() => {
                    setDeleteTarget(character);
                    setConfirmName('');
                  }}
                >
                  删除
                </button>
              </div>
            </li>
          ))}
      </ul>
      <form className="char-create" onSubmit={(event) => void handleCreate(event)}>
        <p className="char-create__caption">创建角色</p>
        <div className="char-create__row">
          <label className="field field--name">
            角色名
            <input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              maxLength={MAX_NAME_LENGTH}
              placeholder={`≤${MAX_NAME_LENGTH} 字`}
              required
            />
          </label>
          <label className="field field--class">
            职业
            <select
              value={newClass}
              onChange={(event) => setNewClass(event.target.value as CharacterClassKind)}
            >
              <option value="warrior">战士</option>
              <option value="mage">法师</option>
              <option value="taoist">道士</option>
            </select>
          </label>
          <button type="submit" className="btn btn--primary" disabled={busy}>
            创建
          </button>
        </div>
      </form>
      {deleteTarget !== null && (
        <div className="modal" role="dialog" aria-modal="true" aria-label="删除角色确认">
          <div className="modal__panel">
            <p className="modal__prompt">
              确定删除角色「{deleteTarget.name}」？输入角色名确认。
            </p>
            <label className="field">
              角色名
              <input
                value={confirmName}
                onChange={(event) => setConfirmName(event.target.value)}
                autoFocus
              />
            </label>
            <div className="modal__actions">
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setDeleteTarget(null);
                  setConfirmName('');
                }}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn--danger"
                disabled={busy || confirmName !== deleteTarget.name}
                onClick={() => void confirmDelete()}
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
