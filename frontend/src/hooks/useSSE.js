import { useEffect } from 'react'

/**
 * useSSEChannel — 订阅 App.jsx 里唯一 EventSource 转发到 window 的事件。
 *
 * App.jsx 建立一条 /api/events 长连接，收到事件后派发 window 自定义事件
 * `jarvis:sse:<channel>`。这里各页面按需监听，组件独立，不会重复建连。
 *
 * 用法：
 *   useSSEChannel('runs', (evt) => loadRuns())
 *   // evt = { channel, payload, ts }
 */
export function useSSEChannel(channel, handler) {
  useEffect(() => {
    if (!handler || !channel) return undefined
    const name = `jarvis:sse:${channel}`
    const on = (e) => handler(e.detail)
    window.addEventListener(name, on)
    return () => window.removeEventListener(name, on)
  }, [channel, handler])
}
