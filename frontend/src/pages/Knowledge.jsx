import { useEffect, useState } from 'react'
import { getKnowledgeTree } from '../api/client'

const PREFIX_LABEL = {
  I: '采集', U: '理解', C: '概念', R: '回忆', A: '应用',
  L: '复盘', T: '认知', P: '项目', MOC: '图谱', S: '技能', '-': '其他',
}

export default function Knowledge() {
  const [tree, setTree] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    getKnowledgeTree().then(setTree).catch((e) => setErr(e.message))
  }, [])

  const total = tree
    ? Object.values(tree).reduce((s, t) => s + (t.count || 0), 0)
    : 0

  return (
    <div style={{ maxWidth: 980, margin: '0 auto' }}>
      <h2 style={{ margin: '0 0 4px' }}>知识库</h2>
      <p className="muted small" style={{ margin: '0 0 16px' }}>
        vault 知识结构总览 · 共 {total} 篇笔记
      </p>

      {err && (
        <div className="card" style={{ borderColor: 'var(--err)' }}>
          <span className="badge err">✕ 加载失败</span>
          <p className="small" style={{ marginTop: 8 }}>{err}</p>
        </div>
      )}

      {!tree && !err && (
        <div className="empty"><span className="spin" /> 正在扫描 vault…</div>
      )}

      {tree && (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>
          {Object.entries(tree).sort((a, b) => b[1].count - a[1].count).map(([top, t]) => (
            <div key={top} className="card" style={{ padding: 14 }}>
              <div className="card-title" style={{ marginBottom: 8 }}>
                <span className="dot" />
                <span className="mono">{top}</span>
                <span className="badge gold" style={{ marginLeft: 'auto' }}>{t.count}</span>
              </div>

              {/* 前缀分布 */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 10 }}>
                {Object.entries(t.prefixes || {}).sort((a, b) => b[1] - a[1]).map(([p, n]) => (
                  <span key={p} className="badge info" style={{ fontSize: 10 }}>
                    {PREFIX_LABEL[p] ?? p} {n}
                  </span>
                ))}
              </div>

              {/* 二级目录 */}
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {Object.entries(t.dirs || {}).sort((a, b) => b[1].count - a[1].count).slice(0, 8).map(([sub, d]) => (
                  <li key={sub} style={{
                    display: 'flex', justifyContent: 'space-between',
                    padding: '4px 0', borderBottom: '1px solid var(--line)', fontSize: 12.5,
                  }}>
                    <span className="muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {sub}
                    </span>
                    <span className="mono" style={{ flexShrink: 0, marginLeft: 8 }}>{d.count}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
