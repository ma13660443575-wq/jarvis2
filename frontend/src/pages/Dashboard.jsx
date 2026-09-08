import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getDashboard, getBriefing, getInsight } from '../api/client'
import { useSSEChannel } from '../hooks/useSSE'

const pct = (v) => (v == null ? '—' : Math.round(v * 100) + '%')
const rateTone = (v) => (v == null ? undefined : v >= 0.8 ? 'ok' : v >= 0.6 ? 'warn' : 'err')

function Stat({ icon, label, value, tone, onClick, hint }) {
  return (
    <div className="card" onClick={onClick}
      style={onClick ? { cursor: 'pointer' } : undefined}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 22 }}>{icon}</span>
        <div>
          <div className="stat-num" style={{ color: tone ? `var(--${tone})` : undefined }}>
            {value}
          </div>
          <div className="stat-label">{label}</div>
        </div>
      </div>
      {hint && <div className="muted small" style={{ marginTop: 6 }}>{hint}</div>}
    </div>
  )
}

const TASK_TONE = {
  '待办': 'warn',
  '进行中': 'info',
  '已完成': 'ok',
}

function renderBriefLine(line) {
  // 简单渲染：把 **加粗** 转成 <strong>
  const parts = line.split('**')
  return parts.map((p, i) => (i % 2 === 1 ? <strong key={i} style={{ color: 'var(--tx-0)' }}>{p}</strong> : p))
}

export default function Dashboard() {
  const nav = useNavigate()
  const [d, setD] = useState(null)
  const [brief, setBrief] = useState(null)
  const [insight, setInsight] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let alive = true
    getDashboard()
      .then((data) => { if (alive) setD(data) })
      .catch((e) => { if (alive) setErr(e.message) })
    getBriefing()
      .then((data) => { if (alive) setBrief(data) })
      .catch(() => {})
    getInsight(7)
      .then((data) => { if (alive) setInsight(data) })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  // 实时：简报更新 / 工作流运行变化自动刷新
  useSSEChannel('briefing', () => {
    getBriefing().then(setBrief).catch(() => {})
  })
  useSSEChannel('runs', () => {
    getDashboard().then(setD).catch(() => {})
  })

  if (err) {
    return (
      <div className="card" style={{ borderColor: 'var(--err)' }}>
        <span className="badge err">✕ 后端连接失败</span>
        <p className="small" style={{ marginTop: 8 }}>
          {err} —— 请确认后端已启动（运行 run.bat）。
        </p>
      </div>
    )
  }

  if (!d) {
    return (
      <div className="empty" style={{ paddingTop: 80 }}>
        <span className="spin" style={{ width: 26, height: 26 }} />
        <p className="muted">正在聚合 JARVIS 状态…</p>
      </div>
    )
  }

  const vault = d.vault || {}
  const contract = d.contract
  const stats = vault.dirs || {}

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 4 }}>
        <h2 style={{ margin: 0 }}>指挥台</h2>
        <span className="muted small">📅 {d.today}</span>
      </div>
      <p className="muted small" style={{ margin: '0 0 18px' }}>
        你的知识操作系统 · 个人 AI 指挥官 —— 看一眼就知道今天该做什么。
      </p>

      {/* 核心状态行 */}
      <div className="grid grid-4">
        <Stat icon="🗄" label="知识库笔记" value={vault.total_notes ?? '—'} tone="gold" onClick={() => nav('/knowledge')} />
        <Stat icon="📥" label="采集箱待处理" value={d.inbox_pending ?? 0} tone={d.inbox_pending ? 'warn' : 'ok'} onClick={() => nav('/command?q=' + encodeURIComponent('处理采集箱'))} hint={d.inbox_pending ? '点击去处理' : '已清空'} />
        <Stat icon="↻" label="今日到期复习" value={d.due_today ?? 0} tone={d.due_today ? 'accent' : 'ok'} onClick={() => nav('/review')} hint={d.due_today ? `${d.due_cards?.length ?? 0} 张展示中` : '今日无到期卡'} />
        <Stat icon="🧠" label="待确认记忆" value={d.memory_pending ?? 0} tone={d.memory_pending ? 'info' : 'ok'} hint={d.memory_pending ? 'AI 控制面板可确认' : '暂无新记忆'} />
      </div>

      {/* 今日简报（每日生成） */}
      {brief?.content && (
        <div className="card" style={{ marginTop: 16, background: 'linear-gradient(135deg, var(--bg-2), var(--bg-1))' }}>
          <div className="card-title">
            <span className="dot" />今日简报
            <span className="muted small" style={{ fontWeight: 400 }}>每天更新 · 一眼看完今天该做什么</span>
            <Link to="/command" className="small" style={{ marginLeft: 'auto', color: 'var(--accent)' }}>去执行 →</Link>
          </div>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px 20px' }}>
            {brief.content.split('\n').filter((l) => l.startsWith('- ')).map((l, i) => (
              <li key={i} style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '6px 0', fontSize: 13, color: 'var(--tx-1)',
              }}>
                {renderBriefLine(l.slice(2))}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 健康度（运行埋点 + token 消耗，数据来自 /api/insight） */}
      {insight && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-title">
            <span className="dot" />健康度
            <span className="muted small" style={{ fontWeight: 400 }}>近 {insight.days} 天 · 运行埋点自动统计</span>
          </div>
          <div className="grid grid-4" style={{ marginBottom: 8 }}>
            <Stat icon="💬" label="对话成功率" value={pct(insight.evals?.by_kind?.chat?.ok_rate)} tone={rateTone(insight.evals?.by_kind?.chat?.ok_rate)} hint={insight.evals?.by_kind?.chat ? `${insight.evals.by_kind.chat.total} 次` : '暂无数据'} />
            <Stat icon="⚡" label="命令成功率" value={pct(insight.evals?.by_kind?.command?.ok_rate)} tone={rateTone(insight.evals?.by_kind?.command?.ok_rate)} hint={insight.evals?.by_kind?.command ? `${insight.evals.by_kind.command.total} 次` : '暂无数据'} />
            <Stat icon="🎙" label="唤醒成功率" value={pct(insight.evals?.by_kind?.wake?.ok_rate)} tone={rateTone(insight.evals?.by_kind?.wake?.ok_rate)} hint={insight.evals?.by_kind?.wake ? `${insight.evals.by_kind.wake.total} 次` : '暂无数据'} />
            <Stat icon="🔢" label="近7天 token" value={(insight.usage?.total_tokens ?? 0).toLocaleString()} tone="gold" hint={insight.usage?.calls ? `${insight.usage.calls} 次调用` : '暂无调用'} />
          </div>
          {(insight.suggestions || []).length > 0 && (
            <div className="small muted" style={{ display: 'flex', flexDirection: 'column', gap: 4, borderTop: '1px solid var(--line)', paddingTop: 8 }}>
              {(insight.suggestions || []).map((s, i) => (
                <span key={i}>· {s}</span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="grid grid-3" style={{ marginTop: 16 }}>
        {/* 待确认记忆 */}
        <div className="card">
          <div className="card-title"><span className="dot" />待确认记忆</div>
          {!d.memory_candidates || d.memory_candidates.length === 0 ? (
            <div className="empty small">暂无待确认项<br /><span className="muted">对话产生候选后会出现在这里</span></div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {d.memory_candidates.slice(0, 4).map((c) => (
                <li key={c.id} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 0', borderBottom: '1px solid var(--line)',
                }}>
                  <span className="badge info" style={{ flexShrink: 0 }}>{c.kind}</span>
                  <span className="small" style={{
                    flex: 1, minWidth: 0, overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>{c.summary}</span>
                </li>
              ))}
            </ul>
          )}
          <Link to="/chat" className="small" style={{ display: 'inline-block', marginTop: 8 }}>去对话页确认 →</Link>
        </div>

        {/* 任务看板 */}
        <div className="card">
          <div className="card-title">
            <span className="dot" />任务看板
            <Link to="/command" className="small" style={{ marginLeft: 'auto', color: 'var(--accent)' }}>+ 新增</Link>
          </div>
          {!d.tasks || d.tasks.length === 0 ? (
            <div className="empty small">暂无任务</div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {d.tasks.slice(0, 5).map((t) => (
                <li key={t.id} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 0', borderBottom: '1px solid var(--line)',
                }}>
                  <span className={'badge ' + (TASK_TONE[t.status] || 'info')} style={{ flexShrink: 0 }}>{t.status}</span>
                  <span className="small" style={{ flex: 1, minWidth: 0 }}>{t.title}</span>
                  <span className="badge gold" style={{ fontSize: 10, flexShrink: 0 }}>{t.priority}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="grid grid-3" style={{ marginTop: 16 }}>
        {/* 技能概览 */}
        <div className="card">
          <div className="card-title">
            <span className="dot" />技能概览
            <Link to="/skills" className="small" style={{ marginLeft: 'auto', color: 'var(--accent)' }}>全部 →</Link>
          </div>
          {!d.skills || d.skills.length === 0 ? (
            <div className="empty small">还没有技能卡<br /><span className="muted">用「技能化」从概念卡沉淀</span></div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {d.skills.slice(0, 4).map((s) => (
                <li key={s.slug} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 0', borderBottom: '1px solid var(--line)',
                }}>
                  <span className="badge gold" style={{ flexShrink: 0 }}>S</span>
                  <span className="small" style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
                  <span className="muted small">{s.trigger?.[0] || ''}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 近期运行 */}
        <div className="card">
          <div className="card-title">
            <span className="dot" />近期运行
            <Link to="/command" className="small" style={{ marginLeft: 'auto', color: 'var(--accent)' }}>命令台 →</Link>
          </div>
          {!d.recent_runs || d.recent_runs.length === 0 ? (
            <div className="empty small">还没有运行记录</div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {d.recent_runs.slice(0, 5).map((r) => (
                <li key={r.id} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 0', borderBottom: '1px solid var(--line)',
                }}>
                  <span className="muted small mono" style={{ flexShrink: 0 }}>{r.workflow}</span>
                  <span className="small" style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.trigger}</span>
                  <span className="muted small">{String(r.created_at).slice(5, 16).replace('T', ' ')}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* vault 结构 */}
        <div className="card">
          <div className="card-title">
            <span className="dot" />vault 结构
            <span className={'badge ' + (contract?.ok ? 'ok' : 'warn')} style={{ marginLeft: 'auto' }}>
              {contract?.ok ? '✓ 契约完整' : `⚠ ${contract?.missing?.length ?? 0} 缺失`}
            </span>
          </div>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {Object.entries(stats).sort((a, b) => b[1] - a[1]).slice(0, 7).map(([dir, n]) => (
              <li key={dir} style={{
                display: 'flex', justifyContent: 'space-between',
                padding: '4px 0', borderBottom: '1px solid var(--line)', fontSize: 12.5,
              }}>
                <span className="mono">{dir}</span>
                <span className="muted">{n}</span>
              </li>
            ))}
          </ul>
          <Link to="/knowledge" className="small" style={{ display: 'inline-block', marginTop: 8 }}>查看完整知识库 →</Link>
        </div>
      </div>
    </div>
  )
}
