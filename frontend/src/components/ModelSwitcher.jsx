// ModelSwitcher — 顶部模型胶囊（当前 LLM 模型 + 快捷切换 + 设置入口）
// 点击胶囊弹出模型下拉（按后端分组）；切换即 PUT /api/settings，Toast 反馈。
import { useEffect, useRef, useState } from 'react'
import { putSettings } from '../api/client'

const BACKEND_LABEL = {
  aliyun: '阿里云百炼',
  deepseek: 'DeepSeek',
  mock: 'Mock',
  auto: '自动',
}

export default function ModelSwitcher({ settings, onChanged, onOpenSettings }) {
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [tip, setTip] = useState('')
  const boxRef = useRef(null)
  const tipTimer = useRef(null)

  const llm = settings?.llm
  const currentModel = (llm && llm.model) || 'qwen-plus'
  const currentBackend = (llm && llm.backend) || 'aliyun'
  const groups = (llm && llm.models) || []

  // 点击外部关闭
  useEffect(() => {
    if (!open) return undefined
    const onDoc = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false) }
    document.addEventListener('pointerdown', onDoc)
    return () => document.removeEventListener('pointerdown', onDoc)
  }, [open])

  const showTip = (msg) => {
    setTip(msg)
    clearTimeout(tipTimer.current)
    tipTimer.current = setTimeout(() => setTip(''), 3200)
  }

  const switchModel = async (backend, model) => {
    setOpen(false)
    if (backend === currentBackend && model === currentModel) return
    setSaving(true)
    try {
      // llm_model 仅 aliyun 后端有效；deepseek 固定 deepseek-chat，不传避免白名单校验失败
      const payload = backend === 'aliyun' || backend === 'auto'
        ? { llm_backend: backend, llm_model: model }
        : { llm_backend: backend }
      await putSettings(payload)
      if (onChanged) onChanged()
      showTip(`已切换 · ${model}`)
    } catch (e) {
      showTip(e.message || '切换失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="model-switcher" ref={boxRef}>
      <button
        className={`ms-pill ${open ? 'open' : ''}`}
        onClick={() => setOpen((v) => !v)}
        disabled={saving}
        title="切换 AI 模型"
      >
        <span className="ms-dot" />
        <span className="ms-model mono">{saving ? '切换中…' : currentModel}</span>
        <span className="ms-caret">▾</span>
      </button>

      {tip && <div className="ms-tip">{tip}</div>}

      {open && (
        <div className="ms-menu">
          <div className="ms-menu-head">
            <span className="ms-menu-title">切换模型</span>
            <button
              className="ms-gear"
              onClick={() => { setOpen(false); if (onOpenSettings) onOpenSettings() }}
              title="完整模型设置"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </button>
          </div>

          {groups.length === 0 && <div className="ms-empty">暂无可用模型</div>}

          {groups.map((g) => (
            <div className="ms-group" key={g.backend}>
              <div className="ms-group-label">{BACKEND_LABEL[g.backend] || g.backend}</div>
              {g.models.map((m) => {
                const active = m === currentModel && g.backend === currentBackend
                return (
                  <button
                    key={m}
                    className={`ms-item ${active ? 'active' : ''}`}
                    onClick={() => switchModel(g.backend, m)}
                  >
                    <span className="mono">{m}</span>
                    {active && <span className="ms-check">✓</span>}
                  </button>
                )
              })}
            </div>
          ))}

          <button className="ms-foot" onClick={() => { setOpen(false); if (onOpenSettings) onOpenSettings() }}>
            打开完整设置 →
          </button>
        </div>
      )}
    </div>
  )
}
