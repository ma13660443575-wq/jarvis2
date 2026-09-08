// EmotiveMatrix — AI 情感 & 意图决策罗盘（右上角）
// 替换原旋转刻度盘装饰，成为 Agent 的"情感 / 态度仪表盘"：
//   · 三环相位罗盘：Logical / Empathetic / Creative 实时比重（环越长占比越高）
//   · 中心十字锁定框：识别到意图时"锁定"并显示 INTENT: CODE (0.96)
//   · 底部状态：SENTIMENT VECTOR / MEMORY MATCH（SQL_ID 记忆关联）
//   · 交互：拖动任一相位环 → 微调 Agent 性格（严谨逻辑 ↔ 极客幽默）
import { useEffect, useRef, useState } from 'react'
import { particleState } from '../lib/particleState'

const CX = 90
const CY = 102
const RINGS = [
  { key: 'logical',  label: 'LOGICAL',    color: '#4cc2ff', r: 74, start: -90 },
  { key: 'empathy',  label: 'EMPATHETIC', color: '#ff6b9d', r: 60, start: 30 },
  { key: 'creative', label: 'CREATIVE',   color: '#ffd54f', r: 46, start: 150 },
]

// 粒子运行态 → 三轴基准（真实驱动：思考=逻辑升，说话=共情升，记忆=创造升）
const MODE_AXIS = {
  idle:       { logical: 0.60, empathy: 0.25, creative: 0.15 },
  listening:  { logical: 0.50, empathy: 0.36, creative: 0.14 },
  thinking:   { logical: 0.74, empathy: 0.11, creative: 0.15 },
  speaking:   { logical: 0.40, empathy: 0.45, creative: 0.15 },
  executing:  { logical: 0.68, empathy: 0.13, creative: 0.19 },
  confirming: { logical: 0.54, empathy: 0.30, creative: 0.16 },
  memory:     { logical: 0.58, empathy: 0.16, creative: 0.26 },
  error:      { logical: 0.48, empathy: 0.36, creative: 0.16 },
}

// step kind → 意图
const INTENT_OF = {
  command: { code: 'WORKFLOW_RUN', conf: 0.97 },
  retrieval: { code: 'KNOWLEDGE_QUERY', conf: 0.96 },
  memory: { code: 'KNOWLEDGE_QUERY', conf: 0.94 },
  exec: { code: 'EXECUTING', conf: 0.93 },
  direct: { code: 'DIRECT_CHAT', conf: 0.88 },
  cognitive: { code: 'INTENT_PARSE', conf: 0.84 },
}

const SENT_OF = {
  idle: 'STABLE', listening: 'ATTENTIVE', thinking: 'FOCUSED', speaking: 'WARM',
  executing: 'DRIVEN', confirming: 'CAUTIOUS', memory: 'REFLECTIVE', error: 'DISTRESS',
}

const lerp = (a, b, t) => a + (b - a) * t

export default function EmotiveMatrix({ thoughts = [], chatOpen = false }) {
  const [axis, setAxis] = useState({ logical: 0.6, empathy: 0.25, creative: 0.15 })
  const [bias, setBias] = useState(0)          // -1..1：性格偏置（+幽默 / -严谨）
  const [memSeq, setMemSeq] = useState(12)     // MEMORY MATCH 序号
  const [intent, setIntent] = useState({ code: 'STANDBY', conf: 0 })
  const [pulse, setPulse] = useState(false)    // 意图锁定脉冲
  const biasRef = useRef(0)
  const intentRef = useRef('STANDBY')
  const seenSteps = useRef(0)
  const dragRef = useRef(null)
  biasRef.current = bias

  // 三轴平滑：粒子运行态 + 性格偏置 → 每 500ms 一帧
  useEffect(() => {
    const id = setInterval(() => {
      const base = MODE_AXIS[particleState.mode] || MODE_AXIS.idle
      setAxis((a) => {
        const t = biasRef.current * 0.28
        const target = {
          logical: Math.max(0.05, base.logical - Math.max(0, t)),
          empathy: base.empathy,
          creative: Math.min(0.9, base.creative + Math.max(0, t)),
        }
        const out = {}
        let sum = 0
        RINGS.forEach(({ key }) => { out[key] = lerp(a[key] || 0, target[key], 0.12); sum += out[key] })
        RINGS.forEach(({ key }) => { out[key] = out[key] / sum })
        return out
      })
    }, 500)
    return () => clearInterval(id)
  }, [])

  // step 事件 → 意图 + 记忆关联
  useEffect(() => {
    if (thoughts.length <= seenSteps.current) return
    const fresh = thoughts.slice(seenSteps.current)
    seenSteps.current = thoughts.length
    fresh.forEach((evt) => {
      if (!evt || !evt.kind) return
      const hit = INTENT_OF[evt.kind]
      if (hit) {
        setIntent({ code: hit.code, conf: hit.conf })
        if (intentRef.current !== hit.code) {
          intentRef.current = hit.code
          setPulse(true)
          setTimeout(() => setPulse(false), 900)
        }
      }
      if (evt.kind === 'retrieval') setMemSeq((n) => n + 1)
    })
  }, [thoughts])

  // ---- 拖动相位环 → 微调性格 ----
  const onPointerDown = (e) => {
    const rect = e.currentTarget.getBoundingClientRect()
    dragRef.current = { startX: e.clientX, startBias: biasRef.current, rectW: rect.width }
    e.currentTarget.setPointerCapture && e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e) => {
    if (!dragRef.current) return
    const { startX, startBias, rectW } = dragRef.current
    const dx = (e.clientX - startX) / (rectW || 180) * 2.4
    setBias(Math.max(-1, Math.min(1, startBias + dx)))
  }
  const onPointerUp = () => { dragRef.current = null }

  const persona = bias > 0.45 ? 'WITTY' : bias < -0.45 ? 'ANALYTICAL' : 'BALANCED'
  const sent = SENT_OF[particleState.mode] || 'STABLE'
  const memLabel = `#${String(480 + memSeq).padStart(4, '0')}`

  return (
    <div className={`em-widget ${chatOpen ? 'chat' : ''}`} aria-hidden="true">
      <div className="em-cap">EMOTIVE &amp; INTENT MATRIX</div>
      <svg
        className="em-matrix"
        viewBox="0 0 180 216"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {/* 外圈刻度（缓慢旋转，氛围用） */}
        <g className="em-ticks">
          {Array.from({ length: 36 }).map((_, i) => {
            const a = (i / 36) * Math.PI * 2
            const r1 = 80
            const r2 = i % 3 === 0 ? 85 : 82
            return (
              <line
                key={i}
                x1={CX + Math.cos(a) * r1}
                y1={CY + Math.sin(a) * r1}
                x2={CX + Math.cos(a) * r2}
                y2={CY + Math.sin(a) * r2}
                className="em-tick"
              />
            )
          })}
        </g>

        {/* 三环相位罗盘 */}
        {RINGS.map((ring) => {
          const C = 2 * Math.PI * ring.r
          const v = axis[ring.key] || 0
          const a = (ring.start * Math.PI) / 180
          const lx = CX + Math.cos(a) * ring.r
          const ly = CY + Math.sin(a) * ring.r
          const tx = CX + Math.cos(a) * (ring.r + 9)
          const ty = CY + Math.sin(a) * (ring.r + 9)
          return (
            <g key={ring.key} className="em-ring" data-ring={ring.key}>
              <circle cx={CX} cy={CY} r={ring.r} className="em-track" />
              <circle
                cx={CX}
                cy={CY}
                r={ring.r}
                className="em-arc"
                stroke={ring.color}
                strokeDasharray={`${Math.max(v * C - 2, 0)} ${C}`}
                transform={`rotate(${ring.start} ${CX} ${CY})`}
              />
              <circle cx={lx} cy={ly} r="2.4" fill={ring.color} className="em-node" />
              <text
                x={tx}
                y={ty}
                className="em-rlabel"
                fill={ring.color}
                textAnchor="middle"
                dominantBaseline="middle"
              >
                {ring.label} {Math.round(v * 100)}
              </text>
            </g>
          )
        })}

        {/* 中心十字锁定框 + 意图 */}
        <g className={`em-lock ${pulse ? 'locked' : ''}`}>
          <line x1={CX - 22} y1={CY} x2={CX - 9} y2={CY} className="em-cross" />
          <line x1={CX + 9} y1={CY} x2={CX + 22} y2={CY} className="em-cross" />
          <line x1={CX} y1={CY - 22} x2={CX} y2={CY - 9} className="em-cross" />
          <line x1={CX} y1={CY + 9} x2={CX} y2={CY + 22} className="em-cross" />
          <circle cx={CX} cy={CY} r="7" className="em-center" />
          <text x={CX} y={CY - 30} className="em-intent-cap" textAnchor="middle">INTENT</text>
          <text x={CX} y={CY - 17} className="em-intent-code" textAnchor="middle">{intent.code}</text>
          <text x={CX} y={CY - 5} className="em-intent-conf" textAnchor="middle">({intent.conf.toFixed(2)})</text>
        </g>

        {/* 底部状态行 */}
        <text x={CX} y={190} className="em-status" textAnchor="middle">
          SENTIMENT VECTOR · {sent}
        </text>
        <text x={CX} y={204} className="em-status" textAnchor="middle">
          MEMORY MATCH · SQL_ID {memLabel}
        </text>
        <text x={CX} y={216} className="em-persona" textAnchor="middle">
          PERSONA · {persona}  {bias !== 0 ? `(${bias > 0 ? '+' : ''}${bias.toFixed(2)})` : ''}
        </text>
      </svg>
      <div className="em-hint">DRAG RINGS TO TUNE · 拖动微调性格</div>
    </div>
  )
}
