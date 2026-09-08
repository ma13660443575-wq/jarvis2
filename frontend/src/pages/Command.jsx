import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  getWorkflows, executeCommand, getRuns, getRunDrafts,
  confirmRun, rejectRun,
} from '../api/client'
import { useSSEChannel } from '../hooks/useSSE'

const STATUS_TONE = {
  draft: 'warn',
  pending: 'info',
  committed: 'ok',
  rejected: 'err',
}
const toneClass = (t) => STATUS_TONE[t] || 'info'

const STATUS_LABEL = {
  draft: '待确认',
  pending: '执行中',
  committed: '已完成',
  rejected: '已拒绝',
}

const PRESET = [
  { text: '处理采集箱', hint: '管道 01→02→03：把 I- 采集卡加工成 U-/C-/R- 卡' },
  { text: '知识提取', hint: '提取 00_Inbox 待处理内容 → 理解 + 概念 + 回忆' },
  { text: '生成回忆', hint: '从 U- 理解卡生成 R- 回忆卡' },
  { text: '系统体检', hint: '07：检查 vault 结构 / 契约 / 数据健康' },
  { text: '情报扫描', hint: '09：抓取 RSS 等外部情报进采集箱' },
  { text: '技能化', hint: '08：把概念卡沉淀成可调用的技能卡 S-' },
]

function fmtTime(iso) {
  if (!iso) return ''
  return iso.replace('T', ' ').slice(5, 16)
}

export default function Command() {
  const [params] = useSearchParams()
  const [input, setInput] = useState(params.get('q') ?? '')
  const [workflows, setWorkflows] = useState([])
  const [runs, setRuns] = useState([])
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)   // 本次执行结果
  const [drafts, setDrafts] = useState([])     // 待确认草稿
  const [preview, setPreview] = useState(null) // 正在预览的草稿文件
  const [err, setErr] = useState(null)
  const [notice, setNotice] = useState(null)
  const resultRef = useRef(null)

  const loadWorkflows = () => getWorkflows().then(setWorkflows).catch(() => setWorkflows([]))
  const loadRuns = () => getRuns().then(setRuns).catch(() => setRuns([]))

  useEffect(() => {
    loadWorkflows()
    loadRuns()
  }, [])

  useEffect(() => {
    // 同步 ?q= 预填（控制面板快捷动作跳转）
    const q = params.get('q')
    if (q) setInput(q)
  }, [params])

  // ?run= 深链：复盘页「技能化」跳转过来直接展开草稿
  useEffect(() => {
    const runId = params.get('run')
    if (!runId) return
    getRunDrafts(parseInt(runId, 10)).then((ds) => {
      if (ds && ds.length) {
        setDrafts(ds); setPreview(ds[0])
        setResult({ run_id: parseInt(runId, 10), status: 'draft' })
        setTimeout(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60)
      }
    }).catch(() => {})
    window.history.replaceState({}, '', '/command') // 清理 URL，避免刷新重复展开
  }, [params])

  // 实时：工作流运行状态变化（提交/完成）自动刷新运行记录
  useSSEChannel('runs', () => loadRuns())

  useEffect(() => {
    if (result && resultRef.current) {
      resultRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [result])

  const run = async (text) => {
    const cmd = (text ?? input).trim()
    if (!cmd || busy) return
    setBusy(true); setErr(null); setNotice(null); setResult(null); setDrafts([]); setPreview(null)
    try {
      const r = await executeCommand(cmd)
      setResult(r)
      if (r.status === 'draft' && r.drafts && r.drafts.length) {
        setDrafts(r.drafts)
        setNotice(`已生成 ${r.drafts.length} 个草稿文件，确认后才会写入知识库。`)
      } else if (r.status === 'committed') {
        const n = (r.written || []).length
        setNotice(`✓ 已完成，写入 ${n} 个文件。可在知识库中查看。`)
      } else if (r.pending_run) {
        setDrafts(r.pending_run.drafts || [])
        setNotice('管道执行到需要确认的环节，请确认草稿后继续。')
      }
      loadRuns()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  const openDrafts = async (runId) => {
    try {
      const ds = await getRunDrafts(runId)
      if (ds && ds.length) {
        setDrafts(ds); setPreview(ds[0]); setResult({ run_id: runId, status: 'draft' })
        resultRef.current?.scrollIntoView({ behavior: 'smooth' })
      }
    } catch { /* ignore */ }
  }

  const doConfirm = async () => {
    const runId = result?.run_id
    if (runId == null) return
    setBusy(true); setErr(null)
    try {
      const r = await confirmRun(runId)
      setNotice(`✓ 已确认写入 ${(r.written || []).length} 个文件，并记录到训练日志。`)
      setDrafts([]); setPreview(null)
      setResult({ ...result, status: 'committed' })
      loadRuns()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  const doReject = async () => {
    const runId = result?.run_id
    if (runId == null) return
    setBusy(true); setErr(null)
    try {
      await rejectRun(runId)
      setNotice('已拒绝该批次草稿，未写入任何文件。')
      setDrafts([]); setPreview(null)
      setResult({ ...result, status: 'rejected' })
      loadRuns()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ maxWidth: 980, margin: '0 auto' }}>
      <h2 style={{ margin: '0 0 4px' }}>命令台</h2>
      <p className="muted small" style={{ margin: '0 0 16px' }}>
        用一句话指挥 JARVIS：执行工作流、调用技能。先生成草稿、你确认后才会写入知识库。
      </p>

      {/* 命令输入 */}
      <div className="card" style={{ padding: 16 }}>
        <div style={{ display: 'flex', gap: 10 }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') run() }}
            placeholder="输入命令，例如「处理采集箱」「帮我整理这份会议纪要」…"
            style={{
              flex: 1, background: 'var(--bg-2)', border: '1px solid var(--line)',
              borderRadius: 10, color: 'var(--tx-0)', padding: '11px 14px', fontSize: 14,
              outline: 'none', fontFamily: 'inherit',
            }}
          />
          <button className="btn primary" onClick={() => run()} disabled={busy || !input.trim()}
            style={{ padding: '0 24px' }}>
            {busy ? <span className="spin" /> : '执行'}
          </button>
        </div>

        {/* 预设命令 */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
          {PRESET.map((p) => (
            <button key={p.text} className="btn" title={p.hint}
              onClick={() => run(p.text)} disabled={busy}
              style={{ fontSize: 12, padding: '4px 12px' }}>
              {p.text}
            </button>
          ))}
        </div>
      </div>

      {/* 反馈 */}
      {err && (
        <div className="card" style={{ borderColor: 'var(--err)', marginTop: 14 }}>
          <span className="badge err">✕ 执行失败</span>
          <p className="small" style={{ margin: '8px 0 0', color: 'var(--tx-1)' }}>{err}</p>
        </div>
      )}
      {notice && (
        <div className="card" style={{ borderColor: 'var(--ok)', marginTop: 14 }}>
          <span className="badge ok">✓ 提示</span>
          <p className="small" style={{ margin: '8px 0 0', color: 'var(--tx-1)' }}>{notice}</p>
        </div>
      )}

      {/* 草稿确认区 */}
      {drafts.length > 0 && (
        <div ref={resultRef} className="card" style={{ marginTop: 14, borderColor: 'var(--warn)' }}>
          <div className="card-title">
            <span className="dot" style={{ background: 'var(--warn)' }} />
            待确认草稿（{drafts.length} 个文件）
            <span className="badge warn" style={{ marginLeft: 'auto' }}>确认前零写入</span>
          </div>

          <div style={{ display: 'flex', gap: 12 }}>
            {/* 文件列表 */}
            <div style={{ width: 230, flexShrink: 0 }}>
              {drafts.map((d, i) => (
                <button key={i}
                  onClick={() => setPreview(d)}
                  className="btn"
                  style={{
                    display: 'block', width: '100%', textAlign: 'left',
                    marginBottom: 6, fontSize: 12, padding: '6px 10px',
                    background: preview === d ? 'var(--bg-3)' : 'var(--bg-2)',
                    borderColor: preview === d ? 'var(--accent)' : 'var(--line)',
                  }}>
                  <span className="mono" style={{ color: 'var(--accent)' }}>{d.action === 'update' ? '改' : '新'}</span>
                  <span className="muted" style={{ display: 'block', wordBreak: 'break-all' }}>{d.rel_path}</span>
                </button>
              ))}
            </div>

            {/* 预览 */}
            <div style={{ flex: 1, minWidth: 0 }}>
              {preview && (
                <>
                  <div className="small muted" style={{ marginBottom: 6 }}>
                    {preview.rel_path} · {preview.action === 'update' ? '更新已有文件' : '新建文件'}
                  </div>
                  <pre style={{
                    background: 'var(--bg-0)', border: '1px solid var(--line)', borderRadius: 8,
                    padding: '12px 14px', maxHeight: 420, overflowY: 'auto',
                    fontSize: 12, fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all', margin: 0, color: 'var(--tx-1)',
                  }}>{preview.content}</pre>
                </>
              )}
            </div>
          </div>

          <div style={{ display: 'flex', gap: 10, marginTop: 14, justifyContent: 'flex-end' }}>
            <button className="btn" onClick={doReject} disabled={busy}>拒绝</button>
            <button className="btn primary" onClick={doConfirm} disabled={busy}>
              {busy ? <span className="spin" /> : '✓ 确认写入知识库'}
            </button>
          </div>
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', marginTop: 20, gap: 16 }}>
        {/* 工作流列表 */}
        <div className="card">
          <div className="card-title"><span className="dot" />工作流与技能</div>
          {workflows.length === 0 ? (
            <div className="empty small">暂无可执行工作流</div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {workflows.map((w) => (
                <li key={w.name} style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '7px 0', borderBottom: '1px solid var(--line)',
                }}>
                  <span className="mono" style={{
                    color: w.name.startsWith('skill:') ? 'var(--accent)' : 'var(--gold)',
                    fontSize: 11.5, width: 74, flexShrink: 0,
                  }}>
                    {w.name.startsWith('skill:') ? '技能' : w.name}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="small" style={{ color: 'var(--tx-0)' }}>{w.title}</div>
                    <div className="muted small" style={{ fontSize: 11 }}>{w.desc}</div>
                  </div>
                  <button className="btn" style={{ padding: '3px 10px', fontSize: 12 }}
                    onClick={() => run(w.title)} disabled={busy}>执行</button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 运行记录 */}
        <div className="card">
          <div className="card-title">
            <span className="dot" />运行记录
            <button className="btn" style={{ marginLeft: 'auto', padding: '2px 10px', fontSize: 11 }}
              onClick={loadRuns}>刷新</button>
          </div>
          {runs.length === 0 ? (
            <div className="empty small">还没有运行记录</div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {runs.slice(0, 12).map((r) => (
                <li key={r.id} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 0', borderBottom: '1px solid var(--line)',
                }}>
                  <span className={'badge ' + toneClass(r.status)} style={{ flexShrink: 0 }}>
                    {STATUS_LABEL[r.status] || r.status}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="small" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.trigger || r.workflow}
                    </div>
                    <div className="muted mono" style={{ fontSize: 10.5 }}>
                      {r.workflow} · {fmtTime(r.created_at)}
                    </div>
                  </div>
                  {r.status === 'draft' && (
                    <button className="btn" style={{ padding: '2px 10px', fontSize: 11 }}
                      onClick={() => openDrafts(r.id)}>查看草稿</button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
