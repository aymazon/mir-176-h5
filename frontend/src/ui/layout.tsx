import { useEffect } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useGameStore } from '../store/game'
import { fetchCharacterMe } from '../api/rest'
import { sseClient } from '../api/sse'

export type TabKey =
  | 'battle'
  | 'inventory'
  | 'equip'
  | 'skill'
  | 'quest'
  | 'teleport'

interface TabDef {
  key: TabKey
  label: string
}

export const TABS: TabDef[] = [
  { key: 'battle', label: '战斗' },
  { key: 'inventory', label: '背包' },
  { key: 'equip', label: '装备' },
  { key: 'skill', label: '技能' },
  { key: 'quest', label: '任务' },
  { key: 'teleport', label: '传送' },
]

/**
 * 手机竖屏单栏布局：
 *  - max-w-[480px] 居中，100dvh 满高
 *  - 底部固定六 Tab 导航（战斗/背包/装备/技能/任务/传送）
 *  - 挂载时做一次会话恢复（GET /api/character/me）+ 建立 SSE 连接
 */
export function AppShell() {
  const character = useGameStore((s) => s.character)

  useEffect(() => {
    void fetchCharacterMe()
    sseClient.connect()
    return () => sseClient.close()
  }, [])

  return (
    <div className="flex h-[100dvh] flex-col bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/80 px-4 py-2">
        <div className="mx-auto flex w-full max-w-[480px] items-center justify-between">
          <span className="text-sm font-semibold tracking-wide text-amber-300">
            传奇 176
          </span>
          <span
            data-testid="header-character"
            className="text-xs text-slate-400"
          >
            {character
              ? `${character.name} · Lv.${character.level}`
              : '未登录'}
          </span>
        </div>
      </header>

      <main
        data-testid="app-shell-main"
        className="mx-auto w-full max-w-[480px] flex-1 overflow-y-auto px-3 pb-20 pt-3"
      >
        <Outlet />
      </main>

      <nav
        data-testid="tab-bar"
        className="fixed inset-x-0 bottom-0 mx-auto max-w-[480px] border-t border-slate-700 bg-slate-900"
      >
        <ul className="grid grid-cols-6">
          {TABS.map((t) => (
            <li key={t.key} className="text-center">
              <NavLink
                to={`/${t.key}`}
                data-testid={`tab-${t.key}`}
                className={({ isActive }) =>
                  [
                    'block py-3 text-xs',
                    isActive
                      ? 'font-semibold text-amber-300'
                      : 'text-slate-400 hover:text-slate-200',
                  ].join(' ')
                }
              >
                {t.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  )
}
