import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useSSEChannel } from '../hooks/useSSE'

/**
 * 右侧常驻 AI 控制面板（可折叠）。
 * 系统状态 + 待确认记忆（可一键确认/忽略）+ 快捷动作。
 */
export default function ControlPanel({ status }) {
  const nav = useNavigate()
  const [collapsed, setCollapsed] = useState(false)
  const [cands, setCands] = useState([])
  const [loading, setLoading] = useState(false)

  const dirs = status?.vault?.dirs ?? null
  const contract = status?.contract

  const loadCands = () => {
    setLoading(true)
    api.get('/api/memory/candidates')
      .then((r) => setCands(r.data || []))
      .catch(() => setCands([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (!collapsed) loadCands()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collapsed])

  // 实时：对话产生新记忆候选 / 确认 / 拒绝时自动刷新
  useSSEChannel('memory', () => { if (!collapsed) loadCands() })

  const act = async (cid, action) => {
    try {
      await api.post(`/api/memory/candidates/${cid}/${action}`, {})
      setCands((cs) => cs.filter((c) => c.id !== cid))
    } catch { /* ignore */ }
  }

  return (
    <aside className={'panel' + (collapsed ? ' collapsed' : '')}>
      <div className="panel-head">
        <span className="p-title">AI 控制面板</span>
        <span className="p-sub">Assistant</span>
        <button
          className="panel-toggle"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? '展开控制面板' : '收起控制面板'}
        >
          {collapsed ? '◀' : '▶'}
        </button>
      </div>

      {!collapsed && (
        <div className="panel-body">
          <div className="card" style={{ padding: 12 }}>
            <div className="card-title"><span className="dot" />系统状态</div>
            {!dirs ? (
              <div className="empty"><span className="spin" /></div>
            ) : (
              <>
                <div className="grid grid-2" style={{ gap: 10 }}>
                  <div>
                    <div className="stat-num">{status.vault.total_notes ?? '—'}</div>
                    <div className="stat-label">vault 笔记</div>
                  </div>
                  <div>
                    <div className="stat-num">{dirs ? Object.keys(dirs).length : '—'}</div>
                    <div className="stat-label">顶层目录</div>
                  </div>
                </div>
                <hr className="divider" />
                {contract && (
                  <div className="small">
                    {contract.ok ? (
                      <span className="badge ok">✓ 路径契约完整</span>
                    ) : (
                      <>
                        <span className="badge warn">⚠ {contract.missing.length} 个契约目录缺失</span>
                        <ul className="contract-list">
                          {contract.missing.slice(0, 5).map((m) => (
                            <li key={m} className="missing">◌ {m}</li>
                          ))}
                        </ul>
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </div>

          <div className="card" style={{ padding: 12 }}>
            <div className="card-title"><span className="dot" />待确认记忆</div>
            {loading ? (
              <div className="empty"><span className="spin" /></div>
            ) : cands.length === 0 ? (
              <div className="empty small">暂无待确认项<br /><span className="muted">对话产生候选后会出现在这里</span></div>
            ) : (
              cands.map((c) => (
                <div key={c.id} style={{
                  display: 'flex', alignItems: 'flex-start', gap: 8,
                  padding: '8px 0', borderBottom: '1px solid var(--line)',
                }}>
                  <span className="badge info" style={{ flexShrink: 0 }}>{c.kind}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="small" style={{ lineHeight: 1.4 }}>{c.summary}</div>
                    <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                      <button className="btn primary" style={{ padding: '2px 10px', fontSize: 12 }}
                        onClick={() => act(c.id, 'confirm')}>记住</button>
                      <button className="btn" style={{ padding: '2px 10px', fontSize: 12 }}
                        onClick={() => act(c.id, 'reject')}>忽略</button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="card" style={{ padding: 12 }}>
            <div className="card-title"><span className="dot" />快捷动作</div>
            <button className="btn" style={{ width: '100%', marginBottom: 8 }}
              onClick={() => nav('/command?q=' + encodeURIComponent('处理采集箱'))}>
              处理采集箱
            </button>
            <button className="btn" style={{ width: '100%', marginBottom: 8 }}
              onClick={() => nav('/command?q=' + encodeURIComponent('知识提取'))}>
              知识提取
            </button>
            <button className="btn" style={{ width: '100%' }}
              onClick={() => nav('/review')}>
              今日复习
            </button>
          </div>
        </div>
      )}
    </aside>
  )
}
