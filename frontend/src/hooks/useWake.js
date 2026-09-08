// useWake.js — 全局后台唤醒 agent 的 React 封装（前端不再本地跑唤醒引擎）
// ----------------------------------------------------------------------
// 暴露：{ wakeEnabled, wakeOn, wstate, wError, toggle, setPaused }
//   wakeEnabled —— 唤醒 agent 是否在线可用（离线则开关置灰）
//   wakeOn       —— agent 常驻监听是否开启
//   wstate       —— agent 状态机 off/offline/listening/armed/recording/asr/executing
//   wError       —— 最近一次错误文案
//   toggle()     —— 开/关 agent 常驻监听（后端转发到 agent 8123）
//   setPaused(v) —— PTT 录音期间暂停/恢复 agent 监听
// 设计：每 2s 轮询 /api/wake/status；SSE 的 wake 事件（回复朗读）由 Core.jsx 订阅。
import { useCallback, useEffect, useRef, useState } from 'react'
import { getWakeStatus, postWakeToggle, postWakePaused } from '../api/client'

const POLL_MS = 2000

export function useWake() {
  const [wakeEnabled, setWakeEnabled] = useState(false)
  const [wakeOn, setWakeOn] = useState(false)
  const [wstate, setWstate] = useState('offline')
  const [wError, setWError] = useState('')
  const pollRef = useRef(null)

  useEffect(() => {
    let alive = true
    const poll = async () => {
      try {
        const st = await getWakeStatus()
        if (!alive) return
        setWakeEnabled(!!st.agent_online)
        setWakeOn(!!st.enabled && st.state !== 'off')
        setWstate(st.state || 'offline')
        setWError('')
      } catch (e) {
        if (!alive) return
        setWakeEnabled(false)
        setWstate('offline')
        setWError((e && e.message) || '唤醒服务未连接')
      }
    }
    poll()
    pollRef.current = setInterval(poll, POLL_MS)
    return () => { alive = false; clearInterval(pollRef.current) }
  }, [])

  const toggle = useCallback(async () => {
    const next = !wakeOn
    setWError('')
    try {
      await postWakeToggle(next)
      setWakeOn(next)
    } catch (e) {
      setWError((e && e.message) || '切换失败：唤醒服务未运行')
    }
  }, [wakeOn])

  const setPaused = useCallback((v) => {
    postWakePaused(v).catch(() => {})
  }, [])

  return { wakeEnabled, wakeOn, wstate, wError, toggle, setPaused }
}
