import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'

function streamChat(question, conversationId, handlers) {
  let body
  try {
    body = JSON.stringify({ question, conversation_id: conversationId ?? null })
  } catch {
    return
  }
  fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
  })
    .then(async (res) => {
      if (!res.ok) {
        let detail = `请求失败 (${res.status})`
        try { detail = (await res.json()).detail ?? detail } catch { /* */ }
        throw new Error(detail)
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let nl
        while ((nl = buf.indexOf('\n')) !== -1) {
          const line = buf.slice(0, nl).trim()
          buf = buf.slice(nl + 1)
          if (!line) continue
          try { handlers.event(JSON.parse(line)) } catch { /* */ }
        }
      }
    })
    .catch((e) => handlers.error(e.message))
}

export default function Chat() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [convId, setConvId] = useState(null)
  const [cands, setCands] = useState([])
  const [err, setErr] = useState(null)
  const scrollRef = useRef(null)

  const send = () => {
    const q = input.trim()
    if (!q || busy) return
    setInput('')
    setErr(null)
    setBusy(true)
    setMessages((m) => [...m, { role: 'user', content: q }])
    setMessages((m) => [...m, { role: 'assistant', content: '', streaming: true }])

    let sources = []
    let doneCands = []
    streamChat(q, convId, {
      event: (evt) => {
        if (evt.type === 'start') {
          setConvId(evt.conversation_id)
        } else if (evt.type === 'sources') {
          sources = evt.sources
        } else if (evt.type === 'delta') {
          setMessages((m) => {
            const copy = [...m]
            const last = copy[copy.length - 1]
            copy[copy.length - 1] = {
              ...last,
              content: last.content + evt.text,
              sources,
            }
            return copy
          })
        } else if (evt.type === 'done') {
          doneCands = (evt.candidates || []).map((c, i) => ({
            ...c,
            cid: (evt.candidate_ids || [])[i],
          }))
          setCands(doneCands)
          setMessages((m) => {
            const copy = [...m]
            copy[copy.length - 1] = {
              ...copy[copy.length - 1],
              streaming: false,
              sources: evt.sources || sources,
            }
            return copy
          })
        }
      },
      error: (msg) => {
        setErr(msg)
        setMessages((m) => {
          const copy = [...m]
          copy[copy.length - 1] = { ...copy[copy.length - 1], streaming: false }
          return copy
        })
        setBusy(false)
      },
    })
  }

  useEffect(() => {
    if (!busy) setBusy(false)
  }, [busy])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  const actCandidate = async (cid, action) => {
    try {
      await api.post(`/api/memory/candidates/${cid}/${action}`, {})
      setCands((cs) => cs.filter((c) => c.cid !== cid))
    } catch (e) { setErr(e.message) }
  }

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', display: 'flex', flexDirection: 'column', height: 'calc(100vh - var(--topbar-h) - 40px)' }}>
      <h2 style={{ margin: '0 0 4px' }}>对话</h2>
      <p className="muted small" style={{ margin: '0 0 14px' }}>
        基于你的知识库问答。支持流式输出、来源标注、记忆候选确认。
      </p>

      <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', border: '1px solid var(--line)', borderRadius: 12, background: 'var(--bg-1)', padding: '16px 18px' }}>
        {messages.length === 0 && (
          <div className="empty" style={{ paddingTop: 60 }}>
            <div className="big">✉</div>
            问点什么吧
            <p className="muted small" style={{ marginTop: 6 }}>
              例如：「我最近的项目进展如何？」「处理采集箱」
            </p>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} style={{
            display: 'flex',
            justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
            marginBottom: 12,
          }}>
            <div style={{
              maxWidth: '78%',
              background: m.role === 'user' ? 'var(--bg-3)' : 'var(--bg-2)',
              border: '1px solid var(--line)',
              borderRadius: 12,
              padding: '10px 14px',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 13.5,
            }}>
              {m.content || (m.streaming ? <span className="spin" /> : '')}
              {m.streaming && m.content && <span className="cursor" />}
              {m.sources && m.sources.length > 0 && (
                <div className="small" style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--line)' }}>
                  <span className="badge info">📎 来源</span>
                  <div style={{ marginTop: 4 }}>
                    {m.sources.map((s) => (
                      <div key={s} className="mono muted" style={{ fontSize: 11 }}>· {s}</div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {err && (
        <div className="small" style={{ color: 'var(--err)', marginTop: 8 }}>⚠ {err}</div>
      )}

      {/* 记忆候选确认卡 */}
      {cands.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div className="card" style={{ padding: 14 }}>
            <div className="card-title"><span className="dot" />发现 {cands.length} 条值得记住的信息</div>
            {cands.map((c) => (
              <div key={c.cid} style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '8px 0', borderBottom: '1px solid var(--line)',
              }}>
                <span className="badge info">{c.kind}</span>
                <span style={{ flex: 1, fontSize: 13 }}>{c.summary}</span>
                <button className="btn primary" style={{ padding: '4px 12px' }}
                  onClick={() => actCandidate(c.cid, 'confirm')}>记住</button>
                <button className="btn" style={{ padding: '4px 12px' }}
                  onClick={() => actCandidate(c.cid, 'reject')}>忽略</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 输入区 */}
      <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
          }}
          placeholder="输入问题，Enter 发送，Shift+Enter 换行…"
          rows={2}
          style={{
            flex: 1, resize: 'none', background: 'var(--bg-2)',
            border: '1px solid var(--line)', borderRadius: 10,
            color: 'var(--tx-0)', padding: '10px 14px', fontSize: 14,
            outline: 'none', fontFamily: 'inherit',
          }}
        />
        <button className="btn primary" onClick={send} disabled={busy || !input.trim()}
          style={{ alignSelf: 'stretch', padding: '0 22px' }}>
          {busy ? <span className="spin" /> : '发送'}
        </button>
      </div>
    </div>
  )
}
