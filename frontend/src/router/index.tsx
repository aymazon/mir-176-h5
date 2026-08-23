import { createBrowserRouter, Navigate } from 'react-router-dom'
import { AppShell } from '../ui/layout'
import BattleTab from '../ui/BattleTab'
import InventoryTab from '../ui/InventoryTab'
import EquipTab from '../ui/EquipTab'
import SkillTab from '../ui/SkillTab'
import QuestTab from '../ui/QuestTab'
import TeleportTab from '../ui/TeleportTab'
import LoginPage from '../ui/LoginPage'
import RegisterPage from '../ui/RegisterPage'

/** 六 Tab + 登录/注册；浏览器历史路由（vite dev/preview 自带 SPA fallback） */
export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/register', element: <RegisterPage /> },
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/battle" replace /> },
      { path: 'battle', element: <BattleTab /> },
      { path: 'inventory', element: <InventoryTab /> },
      { path: 'equip', element: <EquipTab /> },
      { path: 'skill', element: <SkillTab /> },
      { path: 'quest', element: <QuestTab /> },
      { path: 'teleport', element: <TeleportTab /> },
    ],
  },
  { path: '*', element: <Navigate to="/battle" replace /> },
])
