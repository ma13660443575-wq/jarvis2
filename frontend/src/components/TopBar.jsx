import { useEffect, useRef, useState } from 'react'
import { searchNotes } from '../api/client'
import NotePreview from './NotePreview'

export default function TopBar({ title, status }) {
  const cls = status?.online ? 'online' : status?.degraded ? 'degraded' : 'offline'
  const label = status?.online
    ? 'ONLINE'
    : status?.degraded
      ? 'DEGRADED'
      : 'OFFLINE'

  const [q, setQ] = useState('')
  const [results, setResults] = useState([])
  const [mode, setMode] = useState('keyword')
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [active, setActive] = useState(0)
  const [preview, setPreview] = useState(null)
  const boxRef = useRef(null)
  const timerRef = useRef(null)

  // 输入防抖搜索（≥1 字符即搜）
  useEffect(() => {
    const text = q.trim()
    clearTimeout(timerRef.current)
    if (!text) { setResults([]); setOpen(false); setBusy(false); return }
    setBusy(true); setOpen(true)
    timerRef.current = setTimeout(() => {
      searchNotes(text, 8)
        .then((data) => {
          setResults(data || [])
          setActive(0)
          setOpen(true)
        })
        .catch(() => setResults([]))
        .finally(() => setBusy(false))
    }, 220)
    return () => clearTimeout(timerRef.current)
  }, [q])

  // 点击外部关闭
  useEffect(() => {
    const onDown = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  const onKey = (e) => {
    if (e.key === 'Escape') { setOpen(false); setQ('') }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)) }
    if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    if (e.key === 'Enter' && results[active]) {
      e.preventDefault()
      setPreview(results[active].rel_path)
      setOpen(false)
    }
  }

  return (
    <header className="topbar">
      <div className="topbar-title">{title}</div>

      <div className="global-search" ref={boxRef}>
        <span className="icon">⌕</span>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => { if (results.length) setOpen(true) }}
          onKeyDown={onKey}
          placeholder="全局搜索笔记…（回车打开）"
        />

        {open && q.trim() !== '' && (
          <div className="gs-dropdown">
            {busy && results.length === 0 ? (
              <div className="gs-empty"><span className="spin" /> 正在检索…</div>
            ) : results.length === 0 ? (
              <div className="gs-empty">没有找到相关笔记</div>
            ) : (
              <>
                {results.map((r, i) => (
                  <button key={r.rel_path} className={'gs-item' + (i === active ? ' active' : '')}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => { setPreview(r.rel_path); setOpen(false) }}>
                    <div className="gs-title">{r.title || r.rel_path}</div>
                    <div className="gs-path">{r.rel_path}</div>
                    {r.excerpt && <div className="gs-excerpt">{r.excerpt}</div>}
                  </button>
                ))}
                <div className="gs-footer">
                  <span>模式：{mode === 'vector' ? '语义检索' : '关键词检索'}</span>
                  <span>↑↓ 选择 · Enter 打开 · Esc 关闭</span>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      <button
        className="status-light"
        title="点击查看系统状态"
        onClick={() => window.dispatchEvent(new CustomEvent('jarvis:status'))}
      >
        <span className={'status-dot ' + cls} />
        JARVIS · {label}
      </button>

      {preview && <NotePreview path={preview} onClose={() => setPreview(null)} />}
    </header>
  )
}
