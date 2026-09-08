// MemoryStarMap — 记忆/知识核心星图
// 点击「知识核心」→ 粒子球进入 memory 态，星图覆盖层散开成螺旋星点；
// 每个节点 = JARVIS 记住的一篇文档，hover 显示标题 + 路径。
import { useEffect, useMemo, useState } from 'react'
import { getMemoryNodes } from '../api/client'

function hash01(s) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return (h >>> 0) / 4294967295
}

export default function MemoryStarMap({ open, onClose }) {
  const [nodes, setNodes] = useState([])
  const [loading, setLoading] = useState(false)
  const [tip, setTip] = useState(null)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    getMemoryNodes(150)
      .then((list) => setNodes(Array.isArray(list) ? list : []))
      .catch(() => setNodes([]))
      .finally(() => setLoading(false))
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 螺旋星点分布：按 rel_path 哈希决定角度抖动与半径微调，形成星云感
  const laid = useMemo(() => {
    const w = window.innerWidth, h = window.innerHeight
    const cx = w / 2, cy = h / 2
    const R = Math.min(w, h) * 0.42
    const n = nodes.length
    return nodes.map((node, i) => {
      const u = n > 1 ? i / (n - 1) : 0.5
      const angle = u * Math.PI * 6
      const r = R * Math.sqrt(u) * (0.55 + hash01(node.rel_path + 'r') * 0.45)
      const jx = (hash01(node.rel_path + 'x') - 0.5) * 46
      const jy = (hash01(node.rel_path + 'y') - 0.5) * 46
      return {
        ...node,
        x: cx + Math.cos(angle) * r + jx,
        y: cy + Math.sin(angle) * r + jy,
        hot: hash01(node.rel_path + 'h') > 0.92,
      }
    })
  }, [nodes, open])

  if (!open) return null

  return (
    <div className="star-map show">
      <div className="sm-title">MEMORY / KNOWLEDGE CORE</div>
      <button className="sm-close" onClick={onClose} aria-label="关闭星图">✕</button>

      {loading && (
        <div className="sm-empty"><span className="spin" /> 读取记忆节点…</div>
      )}
      {!loading && nodes.length === 0 && (
        <div className="sm-empty">
          <div>知识库索引为空</div>
          <div style={{ fontSize: 11, opacity: 0.6, letterSpacing: '.08em', marginTop: 8 }}>
            让 JARVIS 运行一次「系统体检」建立索引后即可查看
          </div>
        </div>
      )}

      {laid.map((node) => (
        <div
          key={node.rel_path}
          className={`sm-node ${node.hot ? 'hot' : ''}`}
          style={{ left: node.x, top: node.y }}
          onMouseEnter={(e) => setTip({ x: e.clientX, y: e.clientY, title: node.title, path: node.rel_path })}
          onMouseMove={(e) => setTip((t) => (t ? { ...t, x: e.clientX, y: e.clientY } : t))}
          onMouseLeave={() => setTip(null)}
        />
      ))}

      {tip && (
        <div
          className="sm-tip"
          style={{
            left: Math.min(tip.x + 14, window.innerWidth - 260),
            top: Math.min(tip.y + 14, window.innerHeight - 96),
          }}
        >
          {tip.title}
          <span className="sm-tip-path">{tip.path}</span>
        </div>
      )}

      {!loading && nodes.length > 0 && (
        <div className="sm-note">hover 查看记忆 · 共 {nodes.length} 个节点 · ESC 关闭</div>
      )}
    </div>
  )
}
