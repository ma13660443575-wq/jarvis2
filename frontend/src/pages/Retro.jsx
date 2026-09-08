import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getRetro, executeCommand } from '../api/client'
import NotePreview from '../components/NotePreview'

const RUN_TONE = { draft: 'warn', pending: 'info', committed: 'ok', rejected: 'err' }
const RUN_LABEL = { draft: '待确认', pending: '执行中', committed: '已完成', rejected: '已拒绝' }

function fmtTime(iso) {
  if (!iso) return ''
  return String(iso).replace('T', ' ').slice(0, 16)
}

export default function Retro() {
  const nav = useNavigate()
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [busySlug, setBusySlug] = useState(null)
  const [preview, setPreview] = useState(null)
  const [actErr, setActErr] = useState(null)

  const load = () => {
    getRetro().then(setD).catch((e) => setErr(e.message))
  }

  useEffect(() => { load() }, [])

  const skillize = async (c) => {
    setBusySlug(c.rel_path); setActErr(null)
    try {
      const r = await executeCommand('技能化', { concept: c.rel_path })
      if (r.status === 'draft' && r.run_id) {
        nav(`/command?run=${r.run_id}`)
      } else {
        setActErr('技能化未产生草稿，请到命令台重试。')
      }
    } catch (e) {
      setActErr(e.message)
    } finally {
      setBusySlug(null)
    }
  }

  if (err) {
    return (
      <div className="card" style={{ borderColor: 'var(--err)' }}>
        <span className="badge err">✕ 加载失败</span>
        <p className="small" style={{ marginTop: 8 }}>{err}</p>
      </div>
    )
  }
  if (!d) {
    return (
      <div className="empty" style={{ paddingTop: 80 }}>
        <span className="spin" style={{ width: 26, height: 26 }} />
        <p className="muted">正在汇总复盘数据…</p>
      </div>
    )
  }

  const rs = d.review_stats || {}

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 4 }}>
        <h2 style={{ margin: 0 }}>复盘迭代</h2>
        <span className="muted small">📅 {d.today}</span>
      </div>
      <p className="muted small" style={{ margin: '0 0 18px' }}>
        回看最近一次迭代：复习表现 · 复盘记录 · 可沉淀技能的概念卡。复盘是知识系统的「损失函数」。
      </p>

      {actErr && (
        <div className="card" style={{ borderColor: 'var(--err)', marginBottom: 16 }}>
          <span className="badge err">✕ 技能化失败</span>
          <p className="small" style={{ margin: '8px 0 0', color: 'var(--tx-1)' }}>{actErr}</p>
        </div>
      )}

      {/* 复习表现 */}
      <div className="grid grid-4">
        <div className="card">
          <div className="stat-num" style={{ color: 'var(--accent)' }}>{rs.total_graded ?? 0}</div>
          <div className="stat-label">累计复习次数</div>
        </div>
        <div className="card">
          <div className="stat-num" style={{ color: 'var(--ok)' }}>{rs.today_graded ?? 0}</div>
          <div className="stat-label">今日复习</div>
        </div>
        <div className="card">
          <div className="stat-num" style={{ color: 'var(--gold)' }}>
            {rs.avg_quality != null ? rs.avg_quality : '—'}
          </div>
          <div className="stat-label">平均记忆质量</div>
        </div>
        <div className="card">
          <div className="stat-num" style={{ color: (rs.due_today ?? 0) ? 'var(--warn)' : 'var(--ok)' }}>
            {rs.due_today ?? 0}
          </div>
          <div className="stat-label">今日到期复习</div>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', marginTop: 16, gap: 16 }}>
        {/* 复盘记录 */}
        <div className="card">
          <div className="card-title">
            <span className="dot" />复盘记录
            <Link to={`/command?q=${encodeURIComponent('复盘迭代')}`} className="small"
              style={{ marginLeft: 'auto', color: 'var(--accent)' }}>跑一次复盘 →</Link>
          </div>
          {d.recent_retros.length === 0 ? (
            <div className="empty small">
              还没有复盘卡
              <br /><span className="muted">工作流跑完后记得「复盘迭代」，把错误沉淀下来。</span>
            </div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {d.recent_retros.slice(0, 6).map((r) => (
                <li key={r.rel_path} style={{
                  display: 'flex', alignItems: 'flex-start', gap: 10,
                  padding: '9px 0', borderBottom: '1px solid var(--line)',
                }}>
                  <span className="badge gold" style={{ flexShrink: 0 }}>L</span>
                  <button className="retro-item" onClick={() => setPreview(r.rel_path)}>
                    <span className="small" style={{ color: 'var(--tx-0)' }}>{r.title}</span>
                    <span className="muted small" style={{ display: 'block', marginTop: 2 }}>
                      {r.created || ''} · {r.summary.slice(0, 40)}…
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 可技能化概念卡 */}
        <div className="card">
          <div className="card-title">
            <span className="dot" />可技能化概念卡
            <span className="badge info" style={{ marginLeft: 'auto' }}>{d.skillizable.length}</span>
          </div>
          {d.skillizable.length === 0 ? (
            <div className="empty small">
              暂无待沉淀的概念卡
              <br /><span className="muted">掌握中的 C- 卡可以沉淀成可调用的技能。</span>
            </div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {d.skillizable.slice(0, 6).map((c) => (
                <li key={c.rel_path} style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '8px 0', borderBottom: '1px solid var(--line)',
                }}>
                  <span className="badge info" style={{ flexShrink: 0 }}>C</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="small" style={{
                      color: 'var(--tx-0)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{c.title}</div>
                    <div className="muted small" style={{
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 1,
                    }}>{c.summary}</div>
                  </div>
                  <button className="btn" style={{ padding: '3px 10px', fontSize: 12, flexShrink: 0 }}
                    disabled={busySlug === c.rel_path}
                    onClick={() => skillize(c)}>
                    {busySlug === c.rel_path ? <span className="spin" /> : '技能化'}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', marginTop: 16, gap: 16 }}>
        {/* 近期运行 */}
        <div className="card">
          <div className="card-title">
            <span className="dot" />近期运行
            <Link to="/command" className="small" style={{ marginLeft: 'auto', color: 'var(--accent)' }}>命令台 →</Link>
          </div>
          {d.recent_runs.length === 0 ? (
            <div className="empty small">还没有运行记录</div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {d.recent_runs.slice(0, 6).map((r) => (
                <li key={r.id} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 0', borderBottom: '1px solid var(--line)',
                }}>
                  <span className={'badge ' + (RUN_TONE[r.status] || 'info')} style={{ flexShrink: 0 }}>
                    {RUN_LABEL[r.status] || r.status}
                  </span>
                  <span className="mono muted" style={{ flexShrink: 0 }}>{r.workflow}</span>
                  <span className="small" style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {r.trigger}
                  </span>
                  <span className="muted small">{fmtTime(r.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 今日复盘提示 */}
        <div className="card" style={{
          background: 'linear-gradient(135deg, var(--bg-2), var(--bg-1))',
          display: 'flex', flexDirection: 'column', justifyContent: 'center',
        }}>
          <div className="card-title"><span className="dot" />建议</div>
          <p className="small" style={{ margin: '0 0 6px', color: 'var(--tx-1)', lineHeight: 1.7 }}>
            {d.inbox_pending > 0
              ? `采集箱还有 ${d.inbox_pending} 条待处理，处理完可以跑一次「复盘迭代」收拢本轮学习。`
              : '采集箱已清空。今天若处理过内容，跑一次「复盘迭代」把经验沉淀进复盘卡。'}
          </p>
          <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
            <button className="btn primary" style={{ fontSize: 12 }}
              onClick={() => nav('/command?q=' + encodeURIComponent('复盘迭代'))}>
              复盘迭代
            </button>
            <button className="btn" style={{ fontSize: 12 }}
              onClick={() => nav('/review')}>
              今日复习
            </button>
          </div>
        </div>
      </div>

      {preview && <NotePreview path={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
