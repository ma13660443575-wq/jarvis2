import { useEffect, useMemo, useState } from 'react'
import { getNote } from '../api/client'

/**
 * NotePreview — 笔记预览弹窗（只读）。
 * 从全局搜索 / 复盘页点进，经 /api/notes 拉正文，轻量渲染 Markdown。
 */
const FM_KEYS = ['type', 'status', 'created', 'updated', 'domain', 'source', 'tags']

function renderInline(text) {
  // 加粗 **x** → <strong>
  const parts = String(text).split('**')
  return parts.map((p, i) => (i % 2 === 1
    ? <strong key={i} style={{ color: 'var(--tx-0)' }}>{p}</strong>
    : p))
}

function renderBody(md) {
  const lines = String(md || '').split('\n')
  const nodes = []
  let i = 0
  let inCode = false
  let codeBuf = []
  let listBuf = []   // 连续的 - / 1. 行归组
  let paraBuf = []

  const flushList = (key) => {
    if (listBuf.length) {
      nodes.push(
        <ul key={key} style={{ listStyle: 'none', margin: '4px 0', padding: 0 }}>
          {listBuf.map((it, j) => (
            <li key={j} style={{ display: 'flex', gap: 8, padding: '2px 0' }}>
              <span style={{ color: 'var(--gold)', flexShrink: 0 }}>{it.num ? `${it.num}.` : '▸'}</span>
              <span>{renderInline(it.text)}</span>
            </li>
          ))}
        </ul>,
      )
      listBuf = []
    }
  }
  const flushPara = (key) => {
    if (paraBuf.length) {
      nodes.push(<p key={key} style={{ margin: '4px 0' }}>{renderInline(paraBuf.join(' '))}</p>)
      paraBuf = []
    }
  }

  for (; i < lines.length; i++) {
    const raw = lines[i]
    const line = raw.trimEnd()

    // 代码块
    if (line.trim().startsWith('```')) {
      if (inCode) {
        nodes.push(
          <pre key={`c${i}`} style={{
            background: 'var(--bg-0)', border: '1px solid var(--line)', borderRadius: 8,
            padding: '10px 12px', overflowX: 'auto', fontSize: 12,
            fontFamily: 'var(--font-mono)', color: 'var(--tx-1)', margin: '4px 0',
          }}>{codeBuf.join('\n')}</pre>,
        )
        codeBuf = []
        inCode = false
      } else {
        flushList(`l${i}`); flushPara(`p${i}`)
        inCode = true
      }
      continue
    }
    if (inCode) { codeBuf.push(line); continue }

    // frontmatter 直接跳过
    if (line.startsWith('---') && nodes.length === 0 && !inCode) {
      if (i === 0 || lines[i - 1].trim() === '') { i += 1; continue }
    }

    const h1 = line.match(/^#\s+(.*)/)
    const h2 = line.match(/^##\s+(.*)/)
    const h3 = line.match(/^###\s+(.*)/)
    const bq = line.match(/^>\s?(.*)/)
    const liNum = line.match(/^\s*(\d+)\.\s+(.*)/)
    const liDash = line.match(/^\s*[-*]\s+(.*)/)

    if (h1) { flushList(`l${i}`); flushPara(`p${i}`); nodes.push(<h3 key={`h${i}`} style={{ margin: '10px 0 4px', color: 'var(--gold)' }}>{renderInline(h1[1])}</h3>); continue }
    if (h2) { flushList(`l${i}`); flushPara(`p${i}`); nodes.push(<h4 key={`h${i}`} style={{ margin: '8px 0 2px', color: 'var(--tx-0)' }}>{renderInline(h2[1])}</h4>); continue }
    if (h3) { flushList(`l${i}`); flushPara(`p${i}`); nodes.push(<h5 key={`h${i}`} style={{ margin: '6px 0 2px', color: 'var(--tx-1)' }}>{renderInline(h3[1])}</h5>); continue }
    if (bq) {
      flushList(`l${i}`); flushPara(`p${i}`)
      nodes.push(
        <div key={`q${i}`} style={{
          borderLeft: '2px solid var(--gold)', background: 'var(--bg-2)',
          padding: '6px 10px', margin: '4px 0', borderRadius: '0 6px 6px 0',
          color: 'var(--tx-1)', fontSize: 13,
        }}>{renderInline(bq[1])}</div>,
      )
      continue
    }
    if (liDash || liNum) {
      flushPara(`p${i}`)
      listBuf.push({ text: (liDash ? liDash[1] : liNum[2]), num: liNum ? parseInt(liNum[1], 10) : null })
      continue
    }
    if (line.trim() === '') { flushList(`l${i}`); flushPara(`p${i}`); continue }
    flushList(`l${i}`)
    paraBuf.push(line.trim())
  }
  flushList('lend'); flushPara('pend')

  return nodes
}

export default function NotePreview({ path, onClose }) {
  const [note, setNote] = useState(null)
  const [err, setErr] = useState(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let alive = true
    setNote(null); setErr(null); setCopied(false)
    getNote(path)
      .then((n) => { if (alive) setNote(n) })
      .catch((e) => { if (alive) setErr(e.message) })
    return () => { alive = false }
  }, [path])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const copyPath = async () => {
    try {
      await navigator.clipboard.writeText(path)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch { /* ignore */ }
  }

  return (
    <div className="np-overlay" onClick={onClose}>
      <div className="np-modal" onClick={(e) => e.stopPropagation()}>
        <div className="np-head">
          <div style={{ minWidth: 0 }}>
            <div className="np-title" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {note?.title || path}
            </div>
            <div className="np-sub mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {path}
            </div>
          </div>
          <button className="btn" style={{ padding: '3px 10px', fontSize: 11, flexShrink: 0 }}
            onClick={copyPath}>{copied ? '✓ 已复制' : '复制路径'}</button>
          <button className="btn" style={{ padding: '3px 10px', fontSize: 11, flexShrink: 0 }}
            onClick={onClose}>✕ 关闭</button>
        </div>

        <div className="np-body">
          {err ? (
            <div className="empty" style={{ padding: 30 }}>
              <span className="badge err">✕ 无法读取笔记</span>
              <p className="muted small" style={{ marginTop: 8 }}>{err}</p>
            </div>
          ) : !note ? (
            <div className="empty" style={{ padding: 30 }}><span className="spin" /> 正在读取…</div>
          ) : (
            <>
              {/* frontmatter 摘要 */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
                {Object.entries(note.fm || {}).filter(([k]) => FM_KEYS.includes(k)).map(([k, v]) => (
                  <span key={k} className="badge info" style={{ fontSize: 10 }}>
                    {k}: {Array.isArray(v) ? v.join(', ') : String(v)}
                  </span>
                ))}
                {note.prefix && <span className="badge gold" style={{ fontSize: 10 }}>{note.prefix}</span>}
              </div>
              <div style={{ color: 'var(--tx-1)', fontSize: 13.5, lineHeight: 1.75 }}>
                {renderBody(note.body)}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
