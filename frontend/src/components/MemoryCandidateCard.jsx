// MemoryCandidateCard — 记忆候选确认卡（对话流内嵌，非功能面板）
export default function MemoryCandidateCard({ cands, onAct }) {
  if (!cands || cands.length === 0) return null
  return (
    <div className="mem-card">
      <div className="mem-card-title">
        <span className="dot" />发现 {cands.length} 条值得记住的信息
      </div>
      {cands.map((c) => (
        <div key={c.cid} className="mem-item">
          <span className="badge info">{c.kind}</span>
          <span className="mem-summary">{c.summary}</span>
          <button className="btn primary btn-sm" onClick={() => onAct(c.cid, 'confirm')}>记住</button>
          <button className="btn btn-sm" onClick={() => onAct(c.cid, 'reject')}>忽略</button>
        </div>
      ))}
    </div>
  )
}
