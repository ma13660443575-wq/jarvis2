// AgentConsole — AI Agent 控制台（左侧"大脑状态"与运行指标）
// 替换原 DiagnosticWidget（CPU/RAM 模拟），展示 Agent 的真实运行状态：
//   · EMBEDDINGS LOADED —— 向量库已加载的嵌入节点数（真实 /api/memory/nodes）
//   · EMOTION VECTOR    —— 当前情绪因子量化值（由粒子运行态驱动，平滑过渡）
//   · MEMORY NODES      —— 本次检索命中的相关记忆上下文条数（从 step 事件解析）
//   · RETRIEVAL SIGNAL   —— 根据真实 step 事件绘制的检索/执行信号（无事件时保持）
//   · 日志终端           —— Agent 真实思考步骤（[MEM ENGINE]/[QUANT LOGIC]/[LLM]…）
import { useEffect, useRef, useState } from 'react'
import { getComputerStatus, getMemoryNodes } from '../api/client'
import { particleState } from '../lib/particleState'

const N = 32
const W = 220
const H = 44

// step kind → 控制台终端标签（对齐用户示例 [Mem Engine] / [Quant Logic] / [LLM]）
const TAG = {
  system: 'JARVIS',
  cognitive: 'QUANT LOGIC',
  retrieval: 'MEM ENGINE',
  memory: 'MEM ENGINE',
  direct: 'CORE',
  command: 'CMD ROUTER',
  exec: 'EXEC',
}

// 运行态 → 情绪目标向量
const EMOTION_MAP = {
  idle:       { Neutral: 0.82, Empathetic: 0.18 },
  listening:  { Neutral: 0.7,  Empathetic: 0.3 },
  thinking:   { Neutral: 0.55, Analytical: 0.45 },
  speaking:   { Empathetic: 0.72, Neutral: 0.28 },
  executing:  { Neutral: 0.6,  Determined: 0.4 },
  confirming: { Neutral: 0.75, Empathetic: 0.25 },
  memory:     { Neutral: 0.66, Analytical: 0.34 },
  error:      { Concerned: 0.58, Neutral: 0.42 },
}

// 情绪因子缩写
const EMO_ABBR = {
  Neutral: 'NT',
  Empathetic: 'EM',
  Analytical: 'AN',
  Determined: 'DE',
  Concerned: 'CN',
}

function lerp(a, b, t) { return a + (b - a) * t }

// 健康度色阶：成功率 ≥80% 绿、≥60% 黄、其余红（与 Dashboard 卡片一致）
const healthTone = (v) => (v == null ? undefined : v >= 0.8 ? '#4ee6a1' : v >= 0.6 ? '#ffd54f' : '#ff6b6b')
const healthPct = (v) => (v == null ? '—' : Math.round(v * 100) + '%')

export default function AgentConsole({ thoughts = [], tokenUsage = 0, insight = null }) {
  const [emb, setEmb] = useState(null)          // EMBEDDINGS LOADED
  const [memNodes, setMemNodes] = useState(0)   // MEMORY NODES（本次检索命中条数）
  const [signal, setSignal] = useState(() => Array(N).fill(0))
  const [emotion, setEmotion] = useState({ Neutral: 0.82, Empathetic: 0.18 })
  const [computer, setComputer] = useState(null)
  const seenSteps = useRef(0)

  // 载入向量库节点数（EMBEDDINGS LOADED）
  useEffect(() => {
    let alive = true
    getMemoryNodes(500)
      .then((nodes) => { if (alive) setEmb(Array.isArray(nodes) ? nodes.length : 0) })
      .catch(() => { if (alive) setEmb(0) })
    return () => { alive = false }
  }, [])

  // 本地电脑观察桥：仅展示真实活动窗口和 UIA 依赖状态，不伪造 CPU/进程数据。
  useEffect(() => {
    let alive = true
    const load = () => {
      getComputerStatus()
        .then((data) => { if (alive) setComputer(data) })
        .catch(() => { if (alive) setComputer(null) })
    }
    load()
    const id = setInterval(load, 5000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // 情绪向量：随粒子运行态平滑过渡（约 400ms 一帧）
  useEffect(() => {
    const id = setInterval(() => {
      const target = EMOTION_MAP[particleState.mode] || EMOTION_MAP.idle
      setEmotion((e) => {
        const keys = new Set([...Object.keys(e), ...Object.keys(target)])
        const out = {}
        let sum = 0
        keys.forEach((k) => {
          const n = lerp(e[k] || 0, target[k] || 0, 0.16)
          out[k] = n
          sum += n
        })
        keys.forEach((k) => { out[k] = out[k] / sum })  // 归一化，总和 ≈ 1
        return out
      })
    }, 400)
    return () => clearInterval(id)
  }, [])

  // step 事件 → 更新 MEMORY NODES + 真实事件信号；清空会话后允许重新计数。
  useEffect(() => {
    if (thoughts.length < seenSteps.current) seenSteps.current = 0
    if (thoughts.length <= seenSteps.current) return
    const fresh = thoughts.slice(seenSteps.current)
    seenSteps.current = thoughts.length
    const scores = []
    fresh.forEach((evt) => {
      if (evt && evt.kind === 'retrieval') {
        const m = /(\d+)/.exec(evt.text)
        if (m) setMemNodes(parseInt(m[1], 10) || 0)
      }
      const count = evt && evt.kind === 'retrieval' ? Number((/(\d+)/.exec(evt.text || '') || [0, 0])[1]) : 0
      const score = evt && evt.kind === 'retrieval'
        ? Math.min(1, Math.max(0, count / 10))
        : evt && evt.kind === 'memory' ? 0.45
          : evt && evt.kind === 'cognitive' ? 0.3
            : evt && evt.kind === 'command' ? 0.22 : 0.12
      scores.push(score)
    })
    if (scores.length) {
      setSignal((s) => scores.reduce((acc, score) => [...acc.slice(1), score], s))
    }
  }, [thoughts])

  const path = signal.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i / (N - 1)) * W},${H - 4 - v * (H - 10)}`).join(' ')
  const area = `${path} L${W},${H} L0,${H} Z`
  const curScore = signal[signal.length - 1]

  const ledClass =
    particleState.mode === 'error' ? 'hot'
      : (particleState.mode === 'thinking' || particleState.mode === 'executing' || particleState.mode === 'asr') ? 'busy'
        : particleState.mode === 'speaking' ? 'speak' : ''

  const emoTop = Object.entries(emotion).sort((a, b) => b[1] - a[1]).slice(0, 2)
  const logs = thoughts.slice(-4)

  return (
    <div className="diag-widget">
      <div className="diag-title">
        <span className={`diag-led ${ledClass}`} />
        AGENT CONSOLE
      </div>

      {/* 核心指标卡：EMBEDDINGS / EMOTION VECTOR / MEMORY NODES */}
      <div className="ac-metrics">
        <div className="ac-metric">
          <div className="ac-metric-val">{emb ?? '—'}</div>
          <div className="ac-metric-cap">EMBEDDINGS</div>
        </div>
        <div className="ac-metric wide">
          <div className="ac-emo-row">
            {emoTop.map(([k, v]) => (
              <span key={k} className="ac-emo">
                {EMO_ABBR[k] || k[0]}<b>{Math.round(v * 100)}</b>
              </span>
            ))}
          </div>
          <div className="ac-metric-cap">EMOTION VECTOR</div>
        </div>
        <div className="ac-metric">
          <div className="ac-metric-val">{memNodes}</div>
          <div className="ac-metric-cap">MEM NODES</div>
        </div>
      </div>

      {computer && (
        <div className="ac-computer">
          <div className="ac-computer-title">
            <span className={`ac-computer-led ${computer.available ? '' : 'off'}`} />
            COMPUTER BRIDGE
          </div>
          <div className="ac-computer-state">
            {computer.ui_automation?.available ? 'UIA READY' : computer.available ? 'WINDOW OBSERVER' : 'UNAVAILABLE'}
          </div>
          {computer.active_window && (
            <>
              <div className="ac-computer-window" title={computer.active_window.title || ''}>
                {computer.active_window.title || '无标题窗口'}
              </div>
              <div className="ac-computer-process">
                {computer.active_window.process_name || computer.active_window.class_name || 'unknown'}
                {computer.active_window.process_id ? ` · PID ${computer.active_window.process_id}` : ''}
              </div>
            </>
          )}
          <div className="ac-computer-foot">
            {computer.ui_automation?.available ? '控件树可读取' : '控件树依赖待安装'}
          </div>
        </div>
      )}

      {/* 健康度：真实埋点（evals/llm_usage · 近7天）*/}
      {insight && (
        <div className="ac-health">
          <div className="ac-health-title">SYSTEM HEALTH · 近{insight.days}天</div>
          <div className="ac-health-grid">
            {[
              { k: 'chat', label: '对话' },
              { k: 'command', label: '命令' },
              { k: 'wake', label: '唤醒' },
            ].map(({ k, label }) => {
              const b = insight.evals?.by_kind?.[k]
              return (
                <div key={k} className="ac-health-cell">
                  <div className="ac-health-val" style={{ color: healthTone(b?.ok_rate) }}>
                    {healthPct(b?.ok_rate)}
                  </div>
                  <div className="ac-health-cap">{label}</div>
                </div>
              )
            })}
            <div className="ac-health-cell">
              <div className="ac-health-val" style={{ color: 'var(--gold)' }}>
                {(insight.usage?.total_tokens ?? 0) >= 10000
                  ? (insight.usage.total_tokens / 1000).toFixed(0) + 'k'
                  : (insight.usage?.total_tokens ?? 0)}
              </div>
              <div className="ac-health-cap">TOKEN</div>
            </div>
          </div>
          {(insight.suggestions || []).slice(0, 1).map((s, i) => (
            <div key={i} className="ac-health-tip">⚠ {s}</div>
          ))}
        </div>
      )}

      {/* 事件折线：真实检索/执行 step 的信号强度，不冒充向量相似度 */}
      <div className="ac-chart">
        <div className="diag-chart-label">RETRIEVAL SIGNAL · {(curScore * 100).toFixed(0)}%</div>
        <svg className="diag-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
          <defs>
            <linearGradient id="acFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgba(255,183,77,0.32)" />
              <stop offset="100%" stopColor="rgba(255,183,77,0)" />
            </linearGradient>
          </defs>
          {[0.25, 0.5, 0.75].map((f) => (
            <line key={f} x1="0" x2={W} y1={H * f} y2={H * f} className="diag-grid" />
          ))}
          <path d={area} fill="url(#acFill)" />
          <path d={path} className="diag-line" />
        </svg>
      </div>

      {/* 日志终端：Agent 真实思考步骤 */}
      <div className="ac-terminal">
        {logs.length === 0 && (
          <div className="ac-log empty">&gt; JARVIS 待命中，等待指令…</div>
        )}
        {logs.map((ln, i) => {
          const label = TAG[ln && ln.kind] || 'CORE'
          return (
            <div key={`${ln.t}-${i}`} className="ac-log">
              <span className="ac-log-arrow">▸</span>
              <span className={`ac-log-tag ${ln.kind || ''}`}>[{label}]</span>
              <span className="ac-log-text">{ln.text}</span>
            </div>
          )
        })}
        <div className="ac-log-foot">TOKENS · {tokenUsage.toLocaleString()}</div>
      </div>
    </div>
  )
}
