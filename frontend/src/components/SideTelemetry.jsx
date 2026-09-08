// SideTelemetry — 左侧诊断遥测轨（Side Diagnostic Telemetry Bar）
// 竖向极细数据轨：
//   · 14 根竖向跳动的半透明青色频谱柱（随粒子 intensity/levels 实时起伏）
//   · 刻度尺 -180° … +180°
//   · [ COGNITIVE ENGINE: ACTIVE ] 可点击循环切换 Agent 模式 DEV / ANALYTICS / OVERLOAD
import { useEffect, useRef, useState } from 'react'
import { particleState } from '../lib/particleState'

const BAR_COUNT = 14
const RULER = [-180, -120, -60, 0, 60, 120, 180]
const MODES = ['DEV', 'ANALYTICS', 'OVERLOAD']

export default function SideTelemetry() {
  const [mode, setMode] = useState(0)
  const barsRef = useRef([])

  // rAF 读共享频谱 → 每根柱高度（口语/粒子/录音实时起伏）
  useEffect(() => {
    let raf = 0
    const bars = barsRef.current
    const tick = () => {
      const lv = particleState.levels
      for (let i = 0; i < BAR_COUNT; i++) {
        const idx = Math.min(63, Math.floor((i / BAR_COUNT) * 63))
        const v = lv[idx]
        const el = bars[i]
        if (el) el.style.height = `${Math.max(6, Math.min(100, v * 100))}%`
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  const activeMode = MODES[mode]

  return (
    <div className="side-telemetry" aria-hidden="true">
      <div className="st-cap">SYS</div>

      {/* 频谱柱 */}
      <div className="st-bars">
        {Array.from({ length: BAR_COUNT }).map((_, i) => (
          <div
            key={i}
            className="st-bar"
            ref={(el) => { barsRef.current[i] = el }}
          />
        ))}
      </div>

      {/* 刻度尺 -180°…+180° */}
      <div className="st-ruler">
        {RULER.map((v) => (
          <div key={v} className="st-ruler-tick">
            <span className="st-ruler-line" />
            <span className="st-ruler-label">{v}°</span>
          </div>
        ))}
      </div>

      {/* 模态状态切换 */}
      <button
        className={`st-mode ${activeMode === 'OVERLOAD' ? 'overload' : ''}`}
        onClick={() => setMode((m) => (m + 1) % MODES.length)}
        title="点击切换 Agent 模式"
        aria-label="切换 Agent 模式"
      >
        <span className="st-mode-led" />
        <span className="st-mode-text">
          COGNITIVE ENGINE
          <b>{activeMode}</b>
        </span>
      </button>

      <div className="st-cap">▤</div>
    </div>
  )
}
