// App — JARVIS 沉浸单页壳
// 职责收窄为：全局 SSE 长连接（转发为 window 事件）+ status/health 轮询 + 渲染 Core。
// 功能页面已全部隐藏进对话（Agent 层自动调用），不再有侧栏 / 顶栏 / 控制面板。
import { useEffect, useState } from 'react'
import Core from './pages/Core'
import { getStatus, getHealth, postWakeHud } from './api/client'

export default function App() {
  const [health, setHealth] = useState(null)

  useEffect(() => {
    const load = () => {
      Promise.all([getStatus(), getHealth()])
        .then(([, h]) => setHealth(h))
        .catch(() => {})
    }
    load()
    const onStatus = () => load()
    window.addEventListener('jarvis:status', onStatus)
    const timer = setInterval(load, 30000)
    return () => { window.removeEventListener('jarvis:status', onStatus); clearInterval(timer) }
  }, [])

  // 唯一 SSE 长连接：把后端事件转发为 window 自定义事件，供订阅方按需监听
  useEffect(() => {
    let es = null
    try { es = new EventSource('/api/events') } catch { return undefined }
    es.onmessage = (e) => {
      let evt
      try { evt = JSON.parse(e.data) } catch { return }
      window.dispatchEvent(new CustomEvent('jarvis:sse', { detail: evt }))
      window.dispatchEvent(new CustomEvent(`jarvis:sse:${evt.channel}`, { detail: evt }))
    }
    return () => { es.close() }
  }, [])

  // HUD 可见状态上报：唤醒 agent 据此决定「HUD 打开时不代读回复、由前端朗读」
  useEffect(() => {
    const report = () => postWakeHud(document.visibilityState === 'visible').catch(() => {})
    report()
    document.addEventListener('visibilitychange', report)
    return () => {
      document.removeEventListener('visibilitychange', report)
      postWakeHud(false).catch(() => {})
    }
  }, [])

  return <Core health={health} />
}
