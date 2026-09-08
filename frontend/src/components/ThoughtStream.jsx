// ThoughtStream — Agent 思考流（终端命令行 · 打字机式）
// 显示最近 5 条 step 事件，最新一条逐字敲出，暗青/金色等宽字，置于右下角。
import { useEffect, useRef, useState } from 'react'

const KIND_LABEL = {
  system: 'JARVIS System',
  cognitive: 'Cognitive Loop',
  retrieval: 'Memory Engine',
  memory: 'Memory Engine',
  direct: 'Cognitive Loop',
  command: 'Command Router',
  exec: 'Executor',
}

export default function ThoughtStream({ thoughts = [] }) {
  const lines = thoughts.slice(-5)
  const [typed, setTyped] = useState('')
  const last = lines[lines.length - 1]
  const typedKey = useRef(null)

  // 最新一条打字机效果；旧行完整展示
  useEffect(() => {
    if (!last) { setTyped(''); return }
    const key = `${last.t}-${last.text}`
    if (typedKey.current === key) return
    typedKey.current = key
    setTyped('')
    const full = last.text || ''
    let i = 0
    const iv = setInterval(() => {
      i++
      setTyped(full.slice(0, i))
      if (i >= full.length) clearInterval(iv)
    }, 10)
    return () => clearInterval(iv)
  }, [last])

  if (!lines.length) return null

  return (
    <div className="thought-stream">
      {lines.map((ln, i) => {
        const isLast = i === lines.length - 1
        const label = KIND_LABEL[ln.kind] || 'JARVIS'
        const txt = isLast ? typed : ln.text
        const typing = isLast && txt.length < (ln.text || '').length
        return (
          <div key={`${ln.t}-${i}`} className={`thought-line ${ln.kind || ''}`}>
            <span className="prompt">&gt;</span>
            <span>[{label}] {txt}</span>
            {typing && <span className="caret">▋</span>}
          </div>
        )
      })}
    </div>
  )
}
