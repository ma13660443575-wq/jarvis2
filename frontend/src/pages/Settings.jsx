import { useEffect, useState } from 'react'
import { getStatus, getHealth } from '../api/client'

function Row({ k, v, mono }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', gap: 16,
      padding: '9px 0', borderBottom: '1px solid var(--line)', fontSize: 13,
    }}>
      <span className="muted" style={{ flexShrink: 0 }}>{k}</span>
      <span className={mono ? 'mono' : ''} style={{ wordBreak: 'break-all', textAlign: 'right' }}>{v}</span>
    </div>
  )
}

export default function Settings() {
  const [status, setStatus] = useState(null)
  const [health, setHealth] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    Promise.all([getStatus(), getHealth()])
      .then(([s, h]) => { setStatus(s); setHealth(h) })
      .catch((e) => setErr(e.message))
  }, [])

  const contract = status?.contract

  return (
    <div style={{ maxWidth: 760, margin: '0 auto' }}>
      <h2 style={{ margin: '0 0 4px' }}>设置</h2>
      <p className="muted small" style={{ margin: '0 0 16px' }}>
        JARVIS 运行配置 · 全部存于本地，不上传任何笔记内容。
      </p>

      {err && (
        <div className="card" style={{ borderColor: 'var(--err)' }}>
          <span className="badge err">✕ 无法连接后端</span>
          <p className="small" style={{ marginTop: 8 }}>{err}</p>
        </div>
      )}

      <div className="card">
        <div className="card-title"><span className="dot" />系统状态</div>
        <Row k="系统" v={health?.status ?? '—'} />
        <Row k="AI 模式" v={status?.ai_mode ?? health?.ai_mode ?? '—'} />
        <Row k="LLM 大脑" v={health?.llm_enabled ? '已接入' : 'Mock 模式（未配置 API Key）'} />
        <Row k="向量检索" v={health?.vector_enabled ? 'ON（本地 Qdrant）' : 'OFF（关键词降级）'} />
      </div>

      <div className="card">
        <div className="card-title"><span className="dot" />vault 路径契约</div>
        <Row k="vault 根目录" v={health?.vault ?? '—'} mono />
        <Row k="契约状态" v={contract?.ok ? '✓ 完整对齐' : `⚠ ${contract?.missing?.length ?? '?'} 个目录缺失`} />
        <div className="muted small" style={{ marginTop: 8 }}>
          {contract?.ok
            ? '所有知识目录都映射到真实 vault，应用写入与你的知识库完全打通。'
            : '缺失目录不会自动创建（避免污染 vault）。技能库 06_Capability 会在首次使用时创建。'}
        </div>
        {contract && !contract.ok && (
          <ul className="contract-list">
            {contract.missing.map((m) => <li key={m} className="missing">◌ {m}</li>)}
          </ul>
        )}
      </div>

      <div className="card">
        <div className="card-title"><span className="dot" />关于</div>
        <Row k="版本" v="JARVIS2 v0.2.0" mono />
        <Row k="形态" v="Web App（先 Web 后桌面，桌面化 P5 预留）" />
        <Row k="数据原则" v="vault 文件为真相，数据库只做检索镜像" />
      </div>
    </div>
  )
}
