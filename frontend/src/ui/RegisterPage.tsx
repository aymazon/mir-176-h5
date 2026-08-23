import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiRegister } from '../api/rest'

/**
 * 注册页占位：表单提交调 POST /api/auth/register（todo 3 契约）。
 * MVP 为单账号单角色：注册成功即建号完成，直接进战斗页。
 */
export default function RegisterPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(ev: FormEvent<HTMLFormElement>) {
    ev.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const result = await apiRegister(username, password)
      if (result.ok) {
        await navigate('/battle')
      } else {
        setError(result.message ?? '注册失败')
      }
    } catch {
      setError('注册失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      data-testid="register-page"
      className="flex min-h-[100dvh] flex-col items-center justify-center bg-slate-950 px-6 text-slate-100"
    >
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-[320px] rounded-xl border border-slate-800 bg-slate-900/70 p-6"
      >
        <h1 className="mb-1 text-center text-xl font-semibold text-amber-300">
          注册建号
        </h1>
        <p className="mb-5 text-center text-xs text-slate-500">
          单账号单角色，注册即创建角色
        </p>

        <label className="mb-3 block text-sm">
          <span className="mb-1 block text-slate-400">账号</span>
          <input
            data-testid="register-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-amber-400"
          />
        </label>

        <label className="mb-4 block text-sm">
          <span className="mb-1 block text-slate-400">密码</span>
          <input
            data-testid="register-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            required
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-amber-400"
          />
        </label>

        {error && (
          <p data-testid="register-error" className="mb-3 text-xs text-red-400">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          data-testid="register-submit"
          className="w-full rounded bg-amber-500 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400 disabled:opacity-50"
        >
          {submitting ? '注册中…' : '创建角色'}
        </button>

        <p className="mt-4 text-center text-xs text-slate-500">
          已有账号？{' '}
          <Link to="/login" data-testid="goto-login" className="text-amber-300 hover:underline">
            去登录
          </Link>
        </p>
      </form>
    </div>
  )
}
