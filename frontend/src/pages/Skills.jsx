import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getSkills, getSkill, executeCommand } from '../api/client'

export default function Skills() {
  const nav = useNavigate()
  const [skills, setSkills] = useState([])
  const [selected, setSelected] = useState(null) // slug
  const [detail, setDetail] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [err, setErr] = useState(null)

  const load = () => {
    getSkills().then(setSkills).catch(() => setSkills([]))
  }
  useEffect(() => { load() }, [])

  useEffect(() => {
    if (!selected) { setDetail(null); return }
    let alive = true
    setDetail(null)
    getSkill(selected).then((d) => { if (alive) setDetail(d) }).catch(() => {})
    return () => { alive = false }
  }, [selected])

  const callSkill = async (slug) => {
    setBusy(true); setErr(null); setNotice(null)
    try {
      // 用第一个触发词作为命令文本（技能 slug 也能被命令台直接识别）
      const trig = (detail?.trigger && detail.trigger[0]) || slug
      await executeCommand(trig)
      setNotice('✓ 技能已执行，进入命令台查看结果。')
      nav('/command')
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  const skillify = async () => {
    setBusy(true); setErr(null); setNotice(null)
    try {
      await executeCommand('技能化', {})
      setNotice('✓ 已生成技能卡草稿，去命令台确认落库。')
      nav('/command')
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  const refresh = async () => {
    await load()
    if (selected) getSkill(selected).then(setDetail).catch(() => {})
  }

  return (
    <div style={{ maxWidth: 980, margin: '0 auto' }}>
      <h2 style={{ margin: '0 0 4px' }}>技能库</h2>
      <p className="muted small" style={{ margin: '0 0 16px' }}>
        把已验证的知识沉淀成可调用的技能卡（06_Capability/S-*），命令台可直接触发。
      </p>

      <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
        <button className="btn" onClick={refresh} disabled={busy}>
          {busy ? <span className="spin" /> : '↻ 重新扫描技能库'}
        </button>
        <button className="btn primary" onClick={skillify} disabled={busy}>
          ⬡ 技能化一条概念卡
        </button>
      </div>

      {notice && <div className="card" style={{ borderColor: 'var(--ok)' }}><span className="badge ok">✓</span> {notice}</div>}
      {err && <div className="card" style={{ borderColor: 'var(--err)' }}><span className="badge err">✕</span> {err}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 16, alignItems: 'start' }}>
        {/* 技能列表 */}
        <div className="card" style={{ padding: 12 }}>
          <div className="card-title"><span className="dot" />技能列表</div>
          {skills.length === 0 ? (
            <div className="empty small">
              <div className="big" style={{ fontSize: 22 }}>⬡</div>
              还没有技能卡
              <p className="muted small" style={{ marginTop: 4 }}>
                点「技能化一条概念卡」从你的知识库生成第一张技能卡。
              </p>
            </div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {skills.map((s) => (
                <li key={s.slug}>
                  <button
                    onClick={() => setSelected(s.slug)}
                    className="btn"
                    style={{
                      display: 'block', width: '100%', textAlign: 'left', marginBottom: 6,
                      padding: '8px 10px', background: selected === s.slug ? 'var(--bg-3)' : 'var(--bg-2)',
                      borderColor: selected === s.slug ? 'var(--accent)' : 'var(--line)',
                    }}
                  >
                    <div style={{ fontSize: 13, color: 'var(--tx-0)' }}>{s.name}</div>
                    <div className="muted small" style={{ fontSize: 11, marginTop: 2 }}>{s.one_liner}</div>
                    <div style={{ marginTop: 5 }}>
                      <span className={'badge ' + (s.status === '可用' ? 'ok' : 'info')} style={{ fontSize: 10 }}>{s.status}</span>
                      <span className="badge info" style={{ fontSize: 10, marginLeft: 4 }}>{s.output === 'draft' ? '草稿确认' : '直出结果'}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 详情 */}
        <div className="card">
          {!detail ? (
            <div className="empty">
              <div className="big">⬡</div>
              {selected ? '加载中…' : '选择左侧一张技能卡查看详情，或点右上角生成新技能。'}
            </div>
          ) : (
            <>
              <div className="card-title">
                <span className="dot" />{detail.name}
                <span className="badge ok" style={{ marginLeft: 'auto' }}>{detail.status}</span>
              </div>

              <p style={{ margin: '0 0 12px', fontSize: 13.5 }}>{detail.one_liner}</p>

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 14 }}>
                {detail.trigger.map((t) => (
                  <span key={t} className="badge gold" style={{ fontSize: 11 }}>“{t}”</span>
                ))}
              </div>

              <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <div className="card-title" style={{ fontSize: 11 }}><span className="dot" />处理流程</div>
                  <div className="small" style={{ whiteSpace: 'pre-wrap', color: 'var(--tx-1)', lineHeight: 1.7 }}>
                    {detail.flow || '（无）'}
                  </div>
                </div>
                <div>
                  <div className="card-title" style={{ fontSize: 11 }}><span className="dot" />输出要求</div>
                  <div className="small" style={{ whiteSpace: 'pre-wrap', color: 'var(--tx-1)', lineHeight: 1.7 }}>
                    {detail.out_req || '（无）'}
                  </div>
                </div>
              </div>

              <hr className="divider" />
              <div className="small muted" style={{ display: 'flex', flexWrap: 'wrap', gap: 14, marginBottom: 14 }}>
                {detail.source_card && <span>来源：{detail.source_card}</span>}
                {detail.confidence && <span>置信度：{detail.confidence}</span>}
                <span className="mono">{detail.rel_path}</span>
              </div>

              <div style={{ display: 'flex', gap: 10 }}>
                <button className="btn primary" onClick={() => callSkill(detail.slug)} disabled={busy}>
                  {busy ? <span className="spin" /> : '⚡ 调用该技能'}
                </button>
                <button className="btn" onClick={() => nav('/command?q=' + encodeURIComponent(detail.name))}>
                  在命令台输入
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
