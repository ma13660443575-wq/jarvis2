import { useEffect, useState } from 'react'
import { getReviewToday, gradeCard } from '../api/client'

const GRADES = [
  { q: 0, label: '完全忘了', tone: 'err' },
  { q: 1, label: '很模糊', tone: 'err' },
  { q: 2, label: '有点印象', tone: 'warn' },
  { q: 3, label: '勉强想起', tone: 'warn' },
  { q: 4, label: '记得但犹豫', tone: 'info' },
  { q: 5, label: '完全记住', tone: 'ok' },
]

export default function Review() {
  const [cards, setCards] = useState([])
  const [idx, setIdx] = useState(0)
  const [show, setShow] = useState(false)
  const [grading, setGrading] = useState(false)
  const [last, setLast] = useState(null) // 刚打分的调度结果
  const [err, setErr] = useState(null)

  const load = () => {
    getReviewToday().then(setCards).catch((e) => setErr(e.message))
  }
  useEffect(() => { load() }, [])

  const card = cards[idx]

  const grade = async (q) => {
    if (!card || grading) return
    setGrading(true); setErr(null); setLast(null)
    try {
      const r = await gradeCard(card.rel_path, q)
      setLast({ q, ...r })
    } catch (e) {
      setErr(e.message)
    } finally {
      setGrading(false)
    }
  }

  const next = () => {
    setShow(false); setLast(null)
    if (idx + 1 < cards.length) setIdx(idx + 1)
    else { setIdx(0); load() } // 复习完毕，重新加载
  }

  if (err && cards.length === 0) {
    return (
      <div className="card" style={{ borderColor: 'var(--err)' }}>
        <span className="badge err">✕ 加载失败</span>
        <p className="small" style={{ marginTop: 8 }}>{err}</p>
      </div>
    )
  }

  if (cards.length === 0) {
    return (
      <div>
        <h2 style={{ margin: '0 0 4px' }}>复习</h2>
        <p className="muted small" style={{ margin: '0 0 16px' }}>按遗忘曲线复习回忆卡（R-），SM-2 自动调度。</p>
        <div className="card">
          <div className="empty">
            <div className="big">🎉</div>
            今日复习已完成
            <p className="muted small" style={{ marginTop: 6 }}>
              到期卡为空。去命令台跑「生成回忆」把新知识变成可复习的卡片。
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ maxWidth: 720, margin: '0 auto' }}>
      <h2 style={{ margin: '0 0 4px' }}>复习</h2>
      <p className="muted small" style={{ margin: '0 0 16px' }}>今日到期 {cards.length} 张 · 先回忆，再点「显示答案」自评。</p>

      {/* 进度条 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
        <div style={{ flex: 1, height: 6, background: 'var(--bg-2)', borderRadius: 3, overflow: 'hidden' }}>
          <div style={{
            height: '100%', width: `${(idx / cards.length) * 100}%`,
            background: 'linear-gradient(90deg, var(--gold), #d99e33)',
            transition: 'width .3s',
          }} />
        </div>
        <span className="muted small mono">{idx + 1} / {cards.length}</span>
      </div>

      <div className="card" key={card.rel_path} style={{ minHeight: 260 }}>
        {/* 题目 */}
        <div className="small muted" style={{ marginBottom: 8 }}>{card.rel_path}</div>
        <div style={{ fontSize: 17, lineHeight: 1.7, fontWeight: 600, marginBottom: 16 }}>
          {card.question}
        </div>

        {/* 答案 */}
        {!show ? (
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <button className="btn primary" onClick={() => setShow(true)}
              style={{ padding: '10px 28px', fontSize: 14 }}>
              显示答案
            </button>
          </div>
        ) : (
          <>
            <div className="card" style={{
              background: 'var(--bg-0)', borderStyle: 'dashed',
              padding: '14px 16px', marginBottom: 4,
            }}>
              <div className="small" style={{ whiteSpace: 'pre-wrap', color: 'var(--tx-1)', lineHeight: 1.7 }}>
                {card.answer || '（卡片未写答案，建议去命令台重新生成回忆卡）'}
              </div>
              {card.linked_card && (
                <div className="muted small" style={{ marginTop: 8 }}>关联：{card.linked_card}</div>
              )}
            </div>

            {!last ? (
              <>
                <div className="small muted" style={{ margin: '14px 0 8px' }}>记得怎么样？自评打分：</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 8 }}>
                  {GRADES.map((g) => (
                    <button key={g.q} className="btn" disabled={grading}
                      onClick={() => grade(g.q)}
                      style={{ padding: '8px 4px', fontSize: 12, textAlign: 'center', display: 'block' }}>
                      <div style={{ fontSize: 16, fontWeight: 700, color: `var(--${g.tone})` }}>{g.q}</div>
                      <div className="muted" style={{ fontSize: 10, lineHeight: 1.3 }}>{g.label}</div>
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <div className="card" style={{ borderColor: 'var(--ok)', marginTop: 14, background: 'var(--bg-0)' }}>
                <div className="small" style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                  <span>间隔 <b className="mono">{last.interval} 天</b></span>
                  <span>轻松度 <b className="mono">{last.easiness}</b></span>
                  <span>下次复习 <b className="mono">{last.due}</b></span>
                </div>
                <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
                  <button className="btn primary" onClick={next}>
                    {idx + 1 < cards.length ? '下一张 →' : '✓ 完成复习'}
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
