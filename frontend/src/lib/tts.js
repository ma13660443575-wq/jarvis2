// lib/tts.js — 混合语音合成（后端 Edge 神经语音优先，本地浏览器语音兜底）
// 彻底弃用火山 TTS。策略：
//   1. 后端 Edge TTS（edge-tts）：电影级 JARVIS 双语音（云健/Ryan），读回真实 FFT 频谱驱动粒子 → 优先
//   2. 本地浏览器 SpeechSynthesis（Web Speech API）：离线/Edge 不可用时兜底
// 两种方案都返回统一句柄 { kind, promise, stop }：
//   promise 在播放自然结束或被 stop() 打断时 resolve；stop() 立即停止并释放资源。
// 粒子波形：
//   - Edge 播放 mp3 → AudioContext + AnalyserNode 读真实频谱驱动
//   - SpeechSynthesis 无法取到真实 FFT → 用「呼吸正弦 + 音节起伏」合成包络驱动
import { particleState } from './particleState'
import * as audio from './audio'
import { postVoiceTts } from '../api/client'

export function speechSynthAvailable() {
  return typeof window !== 'undefined' && typeof window.speechSynthesis !== 'undefined'
}

// ---- 语音列表是异步加载的，等待一次（最多 ~900ms）----
let voicesPromise = null
function getVoices() {
  const synth = window.speechSynthesis
  const list = synth.getVoices()
  if (list && list.length) return Promise.resolve(list)
  if (!voicesPromise) {
    voicesPromise = new Promise((resolve) => {
      let done = false
      const finish = () => {
        if (done) return
        done = true
        synth.removeEventListener('voiceschanged', finish)
        resolve(synth.getVoices() || [])
      }
      synth.addEventListener('voiceschanged', finish)
      setTimeout(finish, 900)
    })
  }
  return voicesPromise
}

/** 挑一个偏「沉稳男声」的中文语音；没有中文就用任意语音。 */
function pickVoice(voices) {
  const zh = voices.filter((v) => v.lang && v.lang.toLowerCase().startsWith('zh'))
  const prefer = zh.find((v) => /kangkang|yunjian|yunyang|康康|云健|云扬/i.test(v.name))
  if (prefer) return prefer
  return zh[0] || voices[0] || null
}

// ---- 合成语音包络：SpeechSynthesis 没有真实 FFT，用呼吸正弦模拟说话起伏 ----
let synEnvelopeRaf = 0
function stopSynEnvelope() {
  cancelAnimationFrame(synEnvelopeRaf)
  particleState.levels.fill(0)
  particleState.intensity = 0
}

function startSynEnvelope() {
  const t0 = performance.now()
  const levels = particleState.levels
  const tick = () => {
    const t = (performance.now() - t0) / 1000
    const breath = 0.5 + 0.5 * Math.sin(t * 2.2)                        // 呼吸节奏
    const syll = 0.55 + 0.45 * Math.abs(Math.sin(t * 8.3 + Math.sin(t * 3.7))) // 音节起伏
    const base = 0.24 + breath * 0.34 * syll
    for (let i = 0; i < levels.length; i++) {
      const k = i / levels.length
      const spec = 0.5 + 0.5 * Math.sin(i * 1.7 + t * 5.2)              // 频谱形状（低→高）
      levels[i] = Math.min(1, Math.max(0.02, base * (0.4 + 0.6 * spec) + Math.random() * 0.09))
    }
    particleState.intensity = Math.min(1, base + 0.1)
    synEnvelopeRaf = requestAnimationFrame(tick)
  }
  synEnvelopeRaf = requestAnimationFrame(tick)
}

/**
 * 本地 SpeechSynthesis 朗读。返回 { kind, promise, stop }。
 * promise 在朗读结束/被打断时 resolve；stop() 立即 cancel 并清理包络。
 */
export async function speakWithSpeechSynth(text, opts = {}) {
  if (!speechSynthAvailable() || !text) return null
  const synth = window.speechSynthesis
  const voices = await getVoices()
  const u = new SpeechSynthesisUtterance(text)
  const voice = pickVoice(voices)
  if (voice) u.voice = voice
  u.lang = voice ? voice.lang : 'zh-CN'
  u.rate = opts.rate ?? 0.98
  u.pitch = opts.pitch ?? 0.92
  u.volume = opts.volume ?? 1

  let stopped = false
  let resolveDone
  const promise = new Promise((res) => { resolveDone = res })
  const cleanup = () => {
    if (stopped) return
    stopped = true
    stopSynEnvelope()
    try { synth.cancel() } catch { /* ignore */ }
    resolveDone(true)
  }
  u.onend = cleanup
  u.onerror = () => cleanup   // canceled / interrupted / 其他错误一律收尾

  synth.speak(u)
  // 合成包络立即启动；实际发声由浏览器接管（有 fade 效果，短前导无感）
  startSynEnvelope()

  return {
    kind: 'speechsynth',
    promise,
    stop: cleanup,
  }
}

// ---- Edge mp3 播放：读真实 FFT 驱动粒子 ----
let edgeRaf = 0
function stopEdgeEnvelope() {
  cancelAnimationFrame(edgeRaf)
  particleState.levels.fill(0)
  particleState.intensity = 0
}

function startEdgeEnvelope(analyser) {
  const freq = new Uint8Array(analyser.frequencyBinCount)
  const levels = particleState.levels
  const tick = () => {
    analyser.getByteFrequencyData(freq)
    let max = 0
    for (let i = 0; i < freq.length; i++) if (freq[i] > max) max = freq[i]
    particleState.intensity = max / 255
    for (let i = 0; i < levels.length; i++) {
      levels[i] = freq[Math.floor((i * freq.length) / levels.length)] / 255
    }
    edgeRaf = requestAnimationFrame(tick)
  }
  edgeRaf = requestAnimationFrame(tick)
}

/**
 * 后端 Edge/DashScope TTS 朗读。返回 { kind, promise, stop }。
 * opts.voice 为 sambert 音色（如 sambert-zhide-v1）；缺省走后端当前 TTS 链。
 */
export async function speakWithEdge(text, opts = {}) {
  const blob = await postVoiceTts(text, opts.voice)
  const handle = audio.playMp3Blob(blob)
  startEdgeEnvelope(handle.analyser)
  const baseStop = handle.stop
  let stopped = false
  const stop = () => {
    if (stopped) return
    stopped = true
    stopEdgeEnvelope()
    baseStop()
  }
  const promise = handle.promise.then(() => stop())
  return {
    kind: 'edge',
    promise,
    stop,
    audio: handle.audio,
  }
}

/**
 * 混合说话：后端 Edge 神经语音优先（电影级音色 + 真实频谱粒子），
 * 离线或 Edge 不可用时回退本地浏览器 SpeechSynthesis。
 * 返回统一句柄；两者都不可用时返回 null（由调用方静默降级）。
 */
export async function speakText(text, opts = {}) {
  const clean = (text || '').trim()
  if (!clean) return null
  const online = typeof navigator === 'undefined' || navigator.onLine !== false
  if (online) {
    try {
      const handle = await speakWithEdge(clean, opts)
      if (handle) return handle
    } catch { /* fallthrough to local */ }
  }
  if (speechSynthAvailable()) {
    try {
      const handle = await speakWithSpeechSynth(clean, opts)
      if (handle) return handle
    } catch { /* ignore */ }
  }
  return null
}
