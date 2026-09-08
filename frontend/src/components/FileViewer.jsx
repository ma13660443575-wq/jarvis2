// FileViewer — 空间文件透视面板
// 点击来源芯片 → 粒子核心发射光束 → 半透明 3D 浮空卡预览文件内容（L 角标 + 渲染 + 关闭）
// 整改：正文走 renderMd 完整渲染（无裸 md 符号）、去除 YAML frontmatter、[[wikilink]] 可点跳转。
import { useEffect, useState } from 'react'
import { getNote } from '../api/client'
import { renderMd, stripFrontmatter } from '../lib/markdown'

export default function FileViewer({ path, onClose, onNavigate }) {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)
  const [leaving, setLeaving] = useState(false)

  useEffect(() => {
    if (!path) return
    setLoading(true)
    setErr(null)
    setContent('')
    setLeaving(false)
    getNote(path)
      .then((d) => setContent((d && (d.body || d.content)) ?? ''))
      .catch((e) => setErr((e && e.message) || '读取失败'))
      .finally(() => setLoading(false))
  }, [path])

  // 点击正文里的 [[wikilink]] → 就地跳转打开目标笔记
  const onBodyClick = (e) => {
    const wl = e.target && e.target.closest && e.target.closest('.wl')
    if (wl && wl.dataset && wl.dataset.path) onNavigate && onNavigate(wl.dataset.path)
  }

  useEffect(() => {
    if (!path) return
    const onKey = (e) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  if (!path) return null

  const close = () => {
    if (leaving) return
    setLeaving(true)
    setTimeout(onClose, 260)
  }

  // 正文：去 frontmatter + 完整 Markdown 渲染（无裸符号）
  const html = content ? renderMd(stripFrontmatter(content)) : ''

  return (
    <div className="file-viewer">
      <div className={`beam ${leaving ? 'fade' : ''}`} />
      <div className="beam-ring" />
      <div className={`file-card ${leaving ? '' : 'show'}`}>
        <div className="fc-corner tl" />
        <div className="fc-corner br" />
        <div className="fc-head">
          <span className="fc-icon" aria-hidden="true">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <path d="M14 2v6h6" />
            </svg>
          </span>
          <span className="fc-title">{path}</span>
          <button className="fc-close" onClick={close} aria-label="关闭预览">✕</button>
        </div>
        <div className="fc-body" onClick={onBodyClick}>
          {loading && <div className="fc-loading"><span className="spin" /> 读取中…</div>}
          {!loading && err && <div className="err-tip">⚠ {err}</div>}
          {!loading && !err && (html || '（空文档）') && (
            <div className="fc-rendered" dangerouslySetInnerHTML={{ __html: html || '' }} />
          )}
          {!loading && !err && !html && '（空文档）'}
        </div>
      </div>
    </div>
  )
}
