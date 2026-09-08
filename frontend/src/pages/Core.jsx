// Core — JARVIS 沉浸单页（粒子核心 + 对话 + 语音）
// 功能全部隐藏：说「处理采集箱」即触发 wf01；普通问题走 RAG。
import { useCallback, useEffect, useRef, useState } from 'react'

// 收起/展开图标（内联 SVG，免依赖）
const ChevronDown = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
)
const ChevronUp = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 15l-6-6-6 6" /></svg>
)
import CoreParticles from '../components/CoreParticles'
import ConversationOverlay from '../components/ConversationOverlay'
import VoiceButton from '../components/VoiceButton'
import WakeToggle from '../components/WakeToggle'
import HudOverlay from '../components/HudOverlay'
import ThoughtStream from '../components/ThoughtStream'
import FileViewer from '../components/FileViewer'
import MemoryStarMap from '../components/MemoryStarMap'
import SideTelemetry from '../components/SideTelemetry'
import AgentConsole from '../components/AgentConsole'
import ModelSwitcher from '../components/ModelSwitcher'
import ModelSettings from '../components/ModelSettings'
import { useVoice } from '../hooks/useVoice'
import { useWake } from '../hooks/useWake'
import { useSSEChannel } from '../hooks/useSSE'
import { particleState } from '../lib/particleState'
import { speakText, speechSynthAvailable } from '../lib/tts'
import {
  confirmRun, rejectRun, confirmComputerAction, rejectComputerAction,
  confirmDesktopTaskStep, rejectDesktopTaskStep,
  api, postVoiceAsr, getInsight, getSettings,
} from '../api/client'

// ---- NDJSON 流式对话 ----
function streamChat(question, conversationId, handlers, signal) {
  let body
  try { body = JSON.stringify({ question, conversation_id: conversationId ?? null }) } catch { return }
  fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        let detail = `请求失败 (${res.status})`
        try { detail = (await res.json()).detail ?? detail } catch { /* */ }
        throw new Error(detail)
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      const dispatchLine = (rawLine) => {
        const line = rawLine.trim()
        if (!line) return
        try { handlers.event(JSON.parse(line)) } catch { /* 忽略不完整/非 NDJSON 行 */ }
      }
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let nl
        while ((nl = buf.indexOf('\n')) !== -1) {
          const line = buf.slice(0, nl)
          buf = buf.slice(nl + 1)
          dispatchLine(line)
        }
      }
      // 最后一条 NDJSON 可能没有换行符；同时 flush TextDecoder 的残留字节。
      buf += decoder.decode()
      dispatchLine(buf)
      handlers.finish && handlers.finish()
    })
    .catch((e) => {
      if (e && e.name === 'AbortError') { handlers.abort && handlers.abort(); return }
      handlers.error(e.message)
    })
}

function computerActionSuccessMessage(action, result = {}) {
  if (action === 'click_control' && result.save_state === 'SAVE_AS_REQUIRED') {
    return '✅ 已触发保存，当前需要选择保存位置。'
  }
  const messages = {
    open_url: '✅ 已请求本机默认浏览器打开网页。',
    activate_window: '✅ 已将目标窗口切换到前台。',
    click_control: '✅ 已点击指定本机控件。',
    set_text: '✅ 已向指定输入框写入文本。',
  }
  return messages[action] || '✅ 本机动作已完成。'
}

export default function Core({ health }) {
  const ttsEnabled = !!(health && health.voice_enabled)   // 后端 Edge TTS（主方案）
  const asrEnabled = !!(health && health.asr_enabled)     // 本地 sherpa-onnx ASR（语音输入）
  const canSpeak = ttsEnabled || speechSynthAvailable()   // Edge 优先，本地浏览器语音兜底
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [convId, setConvId] = useState(null)
  const [cands, setCands] = useState([])
  const [err, setErr] = useState(null)
  const [cmd, setCmd] = useState(null)
  const [actionPlan, setActionPlan] = useState(null)
  const [overlayActive, setOverlayActive] = useState(false)
  const [docked, setDocked] = useState(false)  // 沉浸模式：收起对话浮层，让位给粒子核心
  const [tick, setTick] = useState(0)   // 粒子模式重算触发器（TTS 结束后回到正确状态）
  const [draftHint, setDraftHint] = useState(false)  // 语音识别填入草稿后的短暂提示
  const [thoughts, setThoughts] = useState([])      // Agent 思考流（step 事件）
  const [tokenUsage, setTokenUsage] = useState(0)   // 本会话累计 token 消耗（能量槽）
  const [openFile, setOpenFile] = useState(null)    // 文件浮空预览（rel_path）
  const [starMapOpen, setStarMapOpen] = useState(false)  // 记忆/知识核心星图
  const [insight, setInsight] = useState(null)      // 健康度（evals/llm_usage 埋点，近7天）
  const [settings, setSettings] = useState(null)            // 模型/语音设置（/api/settings）
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false)  // 设置抽屉开关

  // 拉取当前模型/语音设置（首次进入 + 保存后刷新）
  const loadSettings = useCallback(() => {
    getSettings().then(setSettings).catch(() => {})
  }, [])
  useEffect(() => { loadSettings() }, [loadSettings])

  // 连续对话模式（设置里切换）：点一下持续聆听，VAD 自动断句 → 每句自动发送
  const continuousTalk = !!(settings && settings.voice && settings.voice.continuous_talk)
  const continuousTalkRef = useRef(continuousTalk)
  continuousTalkRef.current = continuousTalk

  // Clean Cache：清空本次会话的上下文与 token 能量槽
  const cleanCache = useCallback(() => {
    setMessages([])
    setCands([])
    setCmd(null)
    setActionPlan(null)
    setThoughts([])
    setTokenUsage(0)
    setConvId(null)
  }, [])

  const voice = useVoice({
    enabled: asrEnabled,
    onResult: (text) => {
      if (!text || !text.trim()) return
      if (continuousTalkRef.current) {
        if (speakingRef.current) return   // JARVIS 正在说话 → 忽略（防止听到自己的回复触发回环）
        send(text)                        // 连续对话：识别一句 → 直接发送
      } else {
        onVoiceEnd(text)                  // PTT：填入草稿待用户确认
      }
    },
  })
  const inputRef = useRef(null)       // 输入框引用（语音草稿聚焦用）
  const draftTimer = useRef(null)     // 草稿提示自动消失定时器
  const speakingRef = useRef(false)
  const audioRef = useRef(null)   // 当前语音播放句柄（本地合成 / Edge，供 Barge-in 停止）
  const abortRef = useRef(null)   // 当前对话流 AbortController（供打断）
  const modeRef = useRef(null)    // 当前对话流模式：command(工作流) | rag
  // ===== 全局后台唤醒 agent（前端不再跑本地唤醒引擎；agent 常驻监听麦克风）=====
  // HUD 仅展示 agent 状态 + 开关；唤醒→录音→ASR→执行全在 agent 进程完成。
  // agent 执行完指令会经 SSE 推 reply，HUD 可见时由前端朗读（不可见时 agent 代读）。
  const wake = useWake()

  // ===== 粒子状态（6 态：idle/listening/thinking/speaking/executing/confirming/error）=====
  useEffect(() => {
    if (speakingRef.current) return
    if (starMapOpen) { particleState.mode = 'memory'; return }  // 记忆星图优先
    const ws = wake.wstate
    if (ws === 'armed' || ws === 'recording') particleState.mode = 'listening'
    else if (ws === 'asr') particleState.mode = 'thinking'
    else if (ws === 'executing') particleState.mode = 'executing'
    else if (voice.state === 'recording') particleState.mode = 'listening'
    else if (voice.state === 'asr') particleState.mode = 'thinking'
    else if ((cmd && cmd.status === 'draft') || (actionPlan && actionPlan.status === 'waiting_confirmation')) {
      particleState.mode = 'confirming'
    }
    else if (actionPlan && actionPlan.status === 'executing') particleState.mode = 'executing'
    else if (busy) particleState.mode = modeRef.current === 'command' ? 'executing' : 'thinking'
    else if (voice.state === 'error') particleState.mode = 'error'
    else particleState.mode = 'idle'
  }, [voice.state, busy, cmd, actionPlan, tick, wake.wstate, starMapOpen])

  // ===== 全局按键唤起对话浮层（除输入框/光环外）=====
  useEffect(() => {
    const onKey = (e) => {
      if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      setOverlayActive(true)
      setDocked(false)  // 任意按键唤起对话（退出沉浸模式）
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // ===== 朗读回复 + 波形驱动粒子（本地 SpeechSynthesis 优先，Edge 兜底） =====
  const stopSpeaking = useCallback(() => {
    if (audioRef.current) {
      try { audioRef.current.stop() } catch { /* ignore */ }
      audioRef.current = null
    }
    particleState.levels.fill(0)
    particleState.intensity = 0
    speakingRef.current = false
  }, [])

  const speak = useCallback(async (text) => {
    if (!canSpeak || !text || speakingRef.current) return
    speakingRef.current = true
    particleState.mode = 'speaking'
    try {
      const handle = await speakText(text)
      if (!speakingRef.current) {  // 合成期间已被 Barge-in 打断
        if (handle && handle.stop) { try { handle.stop() } catch { /* ignore */ } }
        return
      }
      audioRef.current = handle
      await handle.promise
    } catch (e) {
      // 本地与 Edge 都失败：文字已展示，仍提示原因（如 Edge 网络不通）
      if (e && e.message) setErr(typeof e.message === 'string' ? e.message : '语音合成失败')
    } finally {
      if (audioRef.current) {
        try { audioRef.current.stop() } catch { /* ignore */ }
        audioRef.current = null
      }
      particleState.levels.fill(0)
      particleState.intensity = 0
      speakingRef.current = false
      setTick((t) => t + 1)  // 触发 useEffect 重算模式（confirming / idle 等）
    }
  }, [canSpeak])

  // ===== 唤醒 agent 回复朗读（SSE wake 频道）=====
  // agent 在后台执行完指令后推 reply；HUD 可见时前端朗读（隐藏时 agent 已代读，避免双声）。
  useSSEChannel('wake', useCallback((evt) => {
    const p = evt.payload || {}
    if (p.action_plan || p.action_result) {
      setActionPlan(p.action_result
        ? { ...(p.action_plan || {}), ...p.action_result, status: p.action_result.status || p.action_plan?.status }
        : p.action_plan)
      setOverlayActive(true)
      setDocked(false)
    }
    if (p.state === 'reply' && p.text && document.visibilityState === 'visible') {
      speak(p.text)
    }
  }, [speak]))

  // ===== 健康度（真实埋点：/api/insight）=====
  // 启动即拉取；每次工作流运行结束（SSE runs）自动刷新，保证浏览器里能看到实时效果。
  useEffect(() => {
    let alive = true
    getInsight(7).then((d) => { if (alive) setInsight(d) }).catch(() => {})
    return () => { alive = false }
  }, [])
  useSSEChannel('runs', useCallback(() => {
    getInsight(7).then(setInsight).catch(() => {})
  }, []))

  // ===== 发送消息（支持 Barge-in：新指令可打断未完成回复）=====
  const send = useCallback((rawText) => {
    const q = (rawText ?? input).trim()
    if (!q) return
    if (abortRef.current) abortRef.current.abort()  // 取消未完成回复（连续对话打断）
    const controller = new AbortController()
    abortRef.current = controller
    setInput('')
    setErr(null)
    setBusy(true)
    setCmd(null)
    setActionPlan(null)
    setCands([])
    setOverlayActive(true)
    setMessages((m) => [...m, { role: 'user', content: q }, { role: 'assistant', content: '', streaming: true }])

    let sources = []
    let replyText = ''
    streamChat(q, convId, {
      event: (evt) => {
        if (evt.type === 'start') {
          // Computer Action terminal stream 不创建 Conversation，不覆盖当前会话 ID。
          if (evt.conversation_id !== undefined && evt.conversation_id !== null) {
            setConvId(evt.conversation_id)
          }
          modeRef.current = evt.mode || null
        } else if (evt.type === 'plan') {
          setActionPlan(evt.plan || null)
        } else if (evt.type === 'computer_action_result') {
          setActionPlan((p) => p ? { ...p, ...evt, status: evt.status || p.status } : p)
        } else if (evt.type === 'step') {
          // Agent 思考流：终端命令行打字机
          setThoughts((ts) => [...ts, evt])
        } else if (evt.type === 'sources') {
          sources = evt.sources
        } else if (evt.type === 'delta') {
          const delta = typeof evt.text === 'string' ? evt.text : ''
          replyText += delta
          setMessages((m) => {
            const c = [...m]
            const last = c[c.length - 1]
            c[c.length - 1] = { ...last, content: last.content + delta, sources }
            return c
          })
        } else if (evt.type === 'done') {
          if (evt.command) setCmd(evt.command)
          if (evt.action_plan) setActionPlan(evt.action_plan)
          const doneCands = (evt.candidates || []).map((c, i) => ({ ...c, cid: (evt.candidate_ids || [])[i] }))
          setCands(doneCands)
          // Token 能量槽：累加本次消耗
          if (evt.token_usage && evt.token_usage.total_tokens) {
            setTokenUsage((t) => t + evt.token_usage.total_tokens)
          }
          setMessages((m) => {
            const c = [...m]
            c[c.length - 1] = { ...c[c.length - 1], streaming: false, sources: evt.sources || sources }
            return c
          })
        }
      },
      finish: () => {
        if (abortRef.current !== controller) return  // 已被新请求取代，忽略过期收尾
        setBusy(false)
        abortRef.current = null
        // 朗读完整回复
        if (replyText.trim()) speak(replyText)
      },
      abort: () => {
        // 用户开口打断：停止思考，消息收尾，等待新指令
        if (abortRef.current !== controller) return  // 已被新请求取代
        setBusy(false)
        abortRef.current = null
        setMessages((m) => {
          const c = [...m]
          const last = c[c.length - 1]
          if (last && last.role === 'assistant') c[c.length - 1] = { ...last, streaming: false }
          return c
        })
      },
      error: (msg) => {
        if (abortRef.current !== controller) return  // 已被新请求取代
        setErr(msg)
        setActionPlan((p) => p ? { ...p, status: 'failed' } : p)
        setMessages((m) => {
          const c = [...m]
          c[c.length - 1] = { ...c[c.length - 1], streaming: false }
          return c
        })
        setBusy(false)
        abortRef.current = null
      },
    }, controller.signal)
  }, [input, convId, speak])

  // ===== 记忆候选操作 =====
  const actCandidate = useCallback(async (cid, action) => {
    try {
      await api.post(`/api/memory/candidates/${cid}/${action}`, {})
      setCands((cs) => cs.filter((c) => c.cid !== cid))
    } catch (e) { setErr(e.message) }
  }, [])

  // ===== 命令草稿确认 =====
  const doConfirmRun = useCallback(async (runId) => {
    try {
      const data = await confirmRun(runId)
      setCmd(null)
      setActionPlan((p) => p ? { ...p, status: 'completed' } : p)
      const written = data.written || []
      const msg = written.length
        ? `✅ 已写入 ${written.length} 个文件：\n${written.map((w) => `· ${w}`).join('\n')}`
        : '✅ 已确认并写入。'
      setMessages((m) => [...m, { role: 'assistant', content: msg }])
    } catch (e) { setErr(e.message) }
  }, [])

  const doRejectRun = useCallback(async (runId) => {
    try {
      await rejectRun(runId)
      setCmd(null)
      setActionPlan((p) => p ? { ...p, status: 'rejected' } : p)
      setMessages((m) => [...m, { role: 'assistant', content: '已拒绝，未写入任何文件。' }])
    } catch (e) { setErr(e.message) }
  }, [])

  // ===== 本地电脑动作确认（所有写操作必须由这条确认链执行） =====
  const doConfirmComputerAction = useCallback(async (actionId) => {
    setActionPlan((p) => p ? { ...p, status: 'executing' } : p)
    try {
      const data = await confirmComputerAction(actionId)
      setActionPlan((p) => p ? {
        ...p,
        ...data,
        status: data.status || 'completed',
      } : p)
      setMessages((m) => [...m, { role: 'assistant', content: computerActionSuccessMessage(data.action, data) }])
    } catch (e) {
      setActionPlan((p) => p ? {
        ...p,
        status: 'failed',
        error_code: e.code || p.error_code,
        error: e.message || p.error,
      } : p)
      setErr(e.message)
    }
  }, [])

  const doRejectComputerAction = useCallback(async (actionId) => {
    try {
      const data = await rejectComputerAction(actionId)
      setActionPlan((p) => p ? { ...p, status: data.status || 'rejected' } : p)
      setMessages((m) => [...m, { role: 'assistant', content: '已取消本机动作，未执行任何操作。' }])
    } catch (e) { setErr(e.message) }
  }, [])

  // ===== Desktop Task Step 确认（必须携带 task_id + step_id + action_id） =====
  const doConfirmDesktopTaskStep = useCallback(async (taskId, stepId, actionId) => {
    setActionPlan((p) => p ? { ...p, status: 'executing', task_state: 'RUNNING' } : p)
    try {
      const data = await confirmDesktopTaskStep(taskId, stepId, actionId)
      const taskPlan = data.task_plan || null
      const result = data.task_result || {}
      setActionPlan(taskPlan)
      const message = result.status === 'WAITING_CONFIRMATION'
        ? '✅ 当前步骤已完成，请确认下一步。'
        : result.status === 'WAITING_USER'
          ? '✅ 已触发保存，请在系统对话框中选择保存位置。'
          : result.status === 'SUCCEEDED'
            ? '✅ 桌面任务已完成。'
            : result.status === 'FAILED'
              ? `⚠ 当前步骤未完成：${result.reason || result.error_code || '执行或验证失败'}`
              : '✅ 当前步骤已处理。'
      setMessages((m) => [...m, { role: 'assistant', content: message }])
    } catch (e) {
      setActionPlan((p) => p ? {
        ...p,
        status: 'failed',
        task_state: 'FAILED',
        error_code: e.code || p.error_code,
        error: e.message || p.error,
      } : p)
      setErr(e.message)
    }
  }, [])

  const doRejectDesktopTaskStep = useCallback(async (taskId, stepId, actionId) => {
    try {
      const data = await rejectDesktopTaskStep(taskId, stepId, actionId)
      setActionPlan(data.task_plan || null)
      setMessages((m) => [...m, { role: 'assistant', content: '已取消桌面任务，未继续执行后续步骤。' }])
    } catch (e) { setErr(e.message) }
  }, [])

  // ===== 语音全链路：识别结果 → 填入输入框成为可编辑草稿（改好后 ↵ 发送），不自动发送 =====
  const onVoiceEnd = useCallback((text) => {
    setInput(text)
    setOverlayActive(true)
    setDocked(false)   // 唤起对话浮层，让用户看到草稿
    setDraftHint(true) // 短暂提示「已识别，可修改后发送」
    clearTimeout(draftTimer.current)
    draftTimer.current = setTimeout(() => setDraftHint(false), 4000)
    requestAnimationFrame(() => { inputRef.current && inputRef.current.focus() })
  }, [])

  useEffect(() => () => clearTimeout(draftTimer.current), [])  // 卸载时清理定时器

  // Barge-in：用户按下说话 → 立即停止朗读/中止思考，切到聆听
  const beginVoice = useCallback(() => {
    stopSpeaking()
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null }
    particleState.mode = 'listening'
    wake.setPaused(true)   // PTT 录音期间暂停唤醒/VAD，避免两路麦克风处理打架
    voice.begin()
  }, [stopSpeaking, voice, wake])

  // 连续对话：点一下持续聆听（VAD 自动断句 → 每句自动发送），再点结束
  const toggleContinuous = useCallback(() => {
    if (voice.continuous) {
      voice.stopContinuous()
      wake.setPaused(false)   // 结束聆听 → 恢复常驻唤醒
    } else {
      stopSpeaking()
      if (abortRef.current) { abortRef.current.abort(); abortRef.current = null }
      particleState.mode = 'listening'
      wake.setPaused(true)    // 持续聆听期间暂停唤醒，避免双麦冲突
      voice.startContinuous()
    }
  }, [voice, stopSpeaking, wake])

  // PTT 结束（离开 recording）→ 恢复常驻聆听
  useEffect(() => {
    if (voice.state !== 'recording') wake.setPaused(false)
  }, [voice.state, wake.setPaused])

  return (
    <div className="core-shell">
      {/* 粒子核心：点击任意处唤起对话浮层 */}
      <div className="core-bg" onClick={() => { setOverlayActive(true); setDocked(false) }}>
        <CoreParticles />
      </div>

      {/* 状态文案（顶部） */}
      <div className={`core-status mode-${particleState.mode}`}>
        {(() => {
          const ws = wake.wstate
          if (ws === 'armed') return 'JARVIS 在听…'
          if (ws === 'recording') return '正在听指令…'
          if (ws === 'asr') return '正在识别指令…'
          if (ws === 'executing') return '正在执行…'
          if (ws === 'loading' || ws === 'listening') return '常驻聆听中 · 说「JARVIS」'
          return voice.text || (busy ? (modeRef.current === 'command' ? '执行中…' : '思考中…') : speakingRef.current ? '在说…' : actionPlan && actionPlan.status === 'executing' ? '执行中…' : (cmd && cmd.status === 'draft') || (actionPlan && actionPlan.status === 'waiting_confirmation') ? '待你确认' : '')
        })()}
      </div>

      {/* 对话浮层（默认隐藏，交互时浮现；可收起为沉浸模式） */}
      <ConversationOverlay
        messages={messages}
        busy={busy}
        input={input}
        inputRef={inputRef}
        draftHint={draftHint}
        onInput={setInput}
        onSend={() => send()}
        err={err}
        cands={cands}
        onCand={actCandidate}
        cmd={cmd}
        plan={actionPlan}
        onConfirmRun={doConfirmRun}
        onRejectRun={doRejectRun}
        onConfirmAction={doConfirmComputerAction}
        onRejectAction={doRejectComputerAction}
        onConfirmTaskStep={doConfirmDesktopTaskStep}
        onRejectTaskStep={doRejectDesktopTaskStep}
        active={overlayActive}
        docked={docked}
        onActivate={() => { setOverlayActive(true); setDocked(false) }}
        onOpenFile={setOpenFile}
      />

      {/* 顶部模型胶囊 + 模型/语音设置抽屉 */}
      <ModelSwitcher settings={settings} onChanged={loadSettings} onOpenSettings={() => setModelSettingsOpen(true)} />
      <ModelSettings
        open={modelSettingsOpen}
        settings={settings}
        onClose={() => setModelSettingsOpen(false)}
        onChanged={loadSettings}
      />

      {/* 四角 HUD + 思考流 + 记忆星图 + 文件浮空预览 + 左侧 Agent 控制台 */}
      <HudOverlay
        tokenUsage={tokenUsage}
        onCleanCache={cleanCache}
        thoughts={thoughts}
        chatOpen={overlayActive && !docked}
        llmModel={(settings && settings.llm && settings.llm.model) || ''}
      />
      <SideTelemetry />
      <AgentConsole thoughts={thoughts} tokenUsage={tokenUsage} insight={insight} />
      <ThoughtStream thoughts={thoughts} />
      <MemoryStarMap open={starMapOpen} onClose={() => setStarMapOpen(false)} />
      <FileViewer path={openFile} onClose={() => setOpenFile(null)} onNavigate={setOpenFile} />

      {/* 沉浸模式开关：右下角收起/展开对话 */}
      <button
        className="dock-toggle"
        onClick={() => setDocked((d) => !d)}
        aria-label={docked ? '展开对话' : '收起对话'}
        title={docked ? '展开对话' : '收起对话'}
      >
        {docked ? <ChevronUp /> : <ChevronDown />}
      </button>

      {/* 常驻聆听开关（光环左侧「耳朵」） */}
      <WakeToggle
        enabled={wake.wakeEnabled}
        on={wake.wakeOn}
        state={wake.wstate}
        error={wake.wError}
        onToggle={wake.toggle}
      />

      {/* 记忆/知识核心开关（光环右侧「星图」，点击粒子球变色散开成星图） */}
      <button
        className={`knowledge-core-btn ${starMapOpen ? 'on' : ''}`}
        onClick={() => setStarMapOpen((v) => !v)}
        aria-label="记忆知识核心"
        title="记忆知识核心"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="2.2" />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" />
        </svg>
        <span className="knowledge-core-tip">记忆知识核心</span>
      </button>

      {/* 语音按钮（ASR 语音输入；continuous=连续对话，否则按住说话） */}
      <VoiceButton
        enabled={asrEnabled}
        state={voice.state}
        continuous={continuousTalk}
        onBegin={beginVoice}
        onEnd={() => voice.end(onVoiceEnd)}
        onToggleContinuous={toggleContinuous}
        onReset={voice.resetError}
      />
    </div>
  )
}
