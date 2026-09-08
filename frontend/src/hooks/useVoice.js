// useVoice — 语音输入状态机（PTT 按住说话 + 连续对话两种模式）
// ============================================================
// PTT（按住说话）：
//   idle → recording（按住）→ asr（识别中）→ 回调 onResult(text) → idle
//   用 startCapture() + postVoiceAsr（一次性；dashscope / 本地 sherpa / 火山）
// 连续对话（点一下持续聆听，VAD 自动断句）：
//   idle → recording（持续聆听）→ 每句整句实时 onResult → 再点停止 → idle
//   用 startCaptureStream() + WebSocket 直传 PCM 到 /api/voice/asr/stream
//   WS 断流自动降级：把已录内容走一次性 postVoiceAsr 兜底
// 任一环节失败 → error，文案人性化。识别结果由调用方送入对话流。
import { useCallback, useRef, useState } from 'react'
import { postVoiceAsr } from '../api/client'
import * as audio from '../lib/audio'
import { createAsrStream } from '../lib/asrStream'

const STATE_TEXT = {
  idle: '',
  recording: '正在听…',
  asr: '正在识别…',
  error: '出错了',
}

export function useVoice({ enabled = false, onResult, onInterim } = {}) {
  const [state, setState] = useState('idle')
  const [error, setError] = useState(null)
  const [interim, setInterim] = useState('')
  const [continuous, setContinuous] = useState(false)   // 是否处于连续对话模式
  const handleRef = useRef(null)      // PTT 录音句柄
  const contRef = useRef(null)        // 连续对话句柄 { stream, capture }
  const busyRef = useRef(false)
  const onResultRef = useRef(onResult)
  const onInterimRef = useRef(onInterim)
  onResultRef.current = onResult
  onInterimRef.current = onInterim

  // ===== PTT：按住说话 =====
  const begin = useCallback(async () => {
    if (!enabled || busyRef.current) return false
    busyRef.current = true
    setError(null)
    try {
      handleRef.current = await audio.startCapture()
      setState('recording')
      return true
    } catch (e) {
      setState('error')
      setError('无法访问麦克风。请允许麦克风权限，或改用文字输入。')
      busyRef.current = false
      return false
    }
  }, [enabled])

  /** 结束录音 → 识别 → onResult(text)。返回是否成功。 */
  const end = useCallback(async (onResultCb) => {
    const handle = handleRef.current
    if (!handle) { busyRef.current = false; return false }
    handleRef.current = null

    let blob = null
    try { blob = audio.stopCapture(handle) } catch { blob = null }
    if (!blob) {
      setState('error')
      setError('没有听到声音，请再试一次。')
      busyRef.current = false
      return false
    }

    setState('asr')
    try {
      const text = await postVoiceAsr(blob)
      if (!text.trim()) {
        setState('error')
        setError('没听清你说什么，请再说一遍。')
        busyRef.current = false
        return false
      }
      setState('idle')
      busyRef.current = false
      const cb = onResultCb || onResultRef.current
      if (cb) cb(text)
      return true
    } catch (e) {
      setState('error')
      setError(e.message || '语音识别失败，请改用文字输入。')
      busyRef.current = false
      return false
    }
  }, [])

  // ===== 连续对话 =====
  /** WS 断流/出错 → 停止采集，把已录内容走一次性识别兜底 */
  const handleStreamFail = useCallback(async (msg) => {
    const cont = contRef.current
    contRef.current = null
    setContinuous(false)
    setInterim('')
    if (!cont) { busyRef.current = false; setState('idle'); return }
    try { cont.capture.stop() } catch { /* ignore */ }
    try { cont.stream.close() } catch { /* ignore */ }

    let blob = null
    try { blob = cont.capture.toWav() } catch { blob = null }
    setState('asr')
    if (blob) {
      try {
        const text = await postVoiceAsr(blob)
        if (text.trim()) {
          setState('idle')
          busyRef.current = false
          const cb = onResultRef.current
          if (cb) cb(text)
          return
        }
      } catch (e) {
        setError(e.message || '语音识别失败，请改用文字输入。')
      }
    } else {
      setError(msg || '语音识别连接中断，请再试一次。')
    }
    setState('error')
    busyRef.current = false
  }, [])

  /** 开始连续对话：开麦 + WS 直传 PCM，每句整句实时 onResult */
  const startContinuous = useCallback(async () => {
    if (!enabled || busyRef.current) return false
    busyRef.current = true
    setError(null)
    setInterim('')
    try {
      const stream = createAsrStream({
        sampleRate: 16000,
        onFinal: (text) => {
          if (onInterimRef.current) onInterimRef.current('')
          const cb = onResultRef.current
          if (cb) cb(text)
        },
        onError: (msg) => handleStreamFail(msg),
        onDone: () => {},
      })
      if (stream.failed) {
        // 同步创建即失败（如浏览器禁 WS）→ onError 已复位状态
        busyRef.current = false
        return false
      }
      const capture = await audio.startCaptureStream((pcm) => stream.sendPcm(pcm))
      contRef.current = { stream, capture }
      setState('recording')
      setContinuous(true)
      return true
    } catch (e) {
      setState('error')
      setError('无法访问麦克风。请允许麦克风权限，或改用文字输入。')
      busyRef.current = false
      return false
    }
  }, [enabled, handleStreamFail])

  /** 结束连续对话：发 stop 收尾，关闭采集与 WS */
  const stopContinuous = useCallback(async () => {
    const cont = contRef.current
    contRef.current = null
    setContinuous(false)
    setInterim('')
    if (!cont) { busyRef.current = false; setState('idle'); return }
    setState('asr')
    try { cont.capture.stop() } catch { /* ignore */ }
    try { cont.stream.stop() } catch { /* ignore */ }
    // 等服务端 done/error 落地，最多 ~1.5s 后强制关闭
    await new Promise((res) => setTimeout(res, 1500))
    try { cont.stream.close() } catch { /* ignore */ }
    setState('idle')
    busyRef.current = false
  }, [])

  const resetError = useCallback(() => {
    setError(null); setState('idle'); busyRef.current = false; setContinuous(false)
  }, [])

  return { state, error, interim, continuous, text: STATE_TEXT[state] || '', begin, end, startContinuous, stopContinuous, resetError }
}
