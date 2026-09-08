// HudOverlay — 四角 HUD（钢铁侠风 · 微小但精致的数据覆盖层）
// 左上：浏览器可获得的设备信息 / 真实后端延迟 / STATUS
// 右上：旋转刻度盘装饰
// 左下：实时时间 / 时区 / MODEL / PING
// 右下：Token 能量槽 + Clean Cache（满则提示清理）
import { useEffect, useState } from 'react'
import { ping } from '../api/client'
import EmotiveMatrix from './EmotiveMatrix'

function fmtTime(d) {
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export default function HudOverlay({ tokenUsage = 0, onCleanCache, thoughts = [], chatOpen = false, llmModel = '' }) {
  // ---- 时间（1s）----
  const [now, setNow] = useState(() => new Date())
  // ---- 浏览器真实可见信息 + 后端 ping ----
  const [pingMs, setPingMs] = useState(null)
  const [online, setOnline] = useState(() => (
    typeof navigator === 'undefined' || navigator.onLine !== false
  ))
  const cores = typeof navigator !== 'undefined' ? navigator.hardwareConcurrency : null
  const deviceMemory = typeof navigator !== 'undefined' ? navigator.deviceMemory : null

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    let alive = true
    const checkPing = async () => {
      const t0 = performance.now()
      try {
        await ping()
        if (alive) {
          setPingMs(Math.max(1, Math.round(performance.now() - t0)))
          setOnline(true)
        }
      } catch {
        if (alive) {
          setPingMs(null)
          setOnline(false)
        }
      }
    }
    const p = setInterval(checkPing, 5000)
    const onOnline = () => setOnline(true)
    const onOffline = () => { setOnline(false); setPingMs(null) }
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)
    checkPing()
    return () => {
      alive = false
      clearInterval(t)
      clearInterval(p)
      window.removeEventListener('online', onOnline)
      window.removeEventListener('offline', onOffline)
    }
  }, [])

  // 只把真实累计 token 做视觉化，不把它冒充成模型 context window 上限。
  const pct = tokenUsage > 0
    ? Math.min(100, Math.max(4, Math.round(Math.log10(tokenUsage + 1) * 20)))
    : 0
  const hot = pct >= 80
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'LOCAL'

  return (
    <>
      {/* 左上：系统状态 */}
      <div className="hud tl">
        <div className="hud-row">CORES: <b>{cores ?? '—'}</b></div>
        <div className="hud-row">DEVICE MEMORY: <b>{deviceMemory ? `${deviceMemory}GB` : '—'}</b></div>
        <div className="hud-row">LATENCY: <b>{pingMs == null ? '—' : `${pingMs}ms`}</b></div>
        <div className="hud-row">
          STATUS: <b className={online ? 'ok' : 'warn'}>{online ? 'ONLINE' : 'OFFLINE'}</b>
          <span className="hud-tick" />
        </div>
      </div>

      {/* 右上：AI 情感 & 意图决策罗盘（问答面板打开时向左滑开避让） */}
      <EmotiveMatrix thoughts={thoughts} chatOpen={chatOpen} />

      {/* 左下：时间 / 时区 / 模型 / 延迟 */}
      <div className="hud bl">
        <div className="hud-row"><b>{fmtTime(now)}</b></div>
        <div className="hud-row">TZ: <b>{tz}</b></div>
        <div className="hud-row">
          MODEL: <b style={{ color: 'var(--gold)' }}>{llmModel || 'auto'}</b>
          <span className="hud-sep" />PING: <b className={online ? 'ok' : 'warn'}>{pingMs == null ? '—' : `${pingMs}ms`}</b>
        </div>
      </div>
      <div className="hud-cross" aria-hidden="true" />

      {/* 右下：Token 能量槽 */}
      <div className="hud br">
        <div className="energy">
          <div className="energy-cap">SESSION TOKEN LOAD · {pct}%</div>
          <div className="energy-bar">
            <div className={`energy-fill ${hot ? 'hot' : ''}`} style={{ width: `${pct}%` }} />
          </div>
          <div className="energy-cap">
            <span>{tokenUsage.toLocaleString()} tok</span>
            <button
              className={`energy-clean ${tokenUsage === 0 ? 'disabled' : ''}`}
              disabled={tokenUsage === 0}
              onClick={onCleanCache}
              title="清空本次会话上下文；上方比例仅用于显示累计 token 负载"
            >
              CLEAN CACHE
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
