// ModelSettings — 模型/语音设置抽屉
// 区块：LLM（后端 + 模型 + AI 推荐）· TTS（引擎 + sambert 音色试听）· ASR（引擎 + 连续对话）· Key 状态
// 保存 → PUT /api/settings → onChanged 通知上层刷新。试听直接播放 mp3，无需保存。
import { useEffect, useRef, useState } from 'react'
import * as audio from '../lib/audio'
import { putSettings, recommendModel, ttsPreview } from '../api/client'

const BACKEND_LABEL = { auto: '自动', aliyun: '阿里云百炼', deepseek: 'DeepSeek', mock: 'Mock' }
const TTS_ENGINE_LABEL = { auto: '自动（DashScope → Edge）', dashscope: 'DashScope 音色', edge: 'Edge 神经语音' }
const ASR_ENGINE_LABEL = { auto: '自动', dashscope: 'DashScope 流式', local: '本地 sherpa', volcano: '火山引擎' }
const SCENE_LABEL = { chat: '日常对话', briefing: '每日简报', memory: '记忆抽取', review: '复习卡片', task: '任务拆解', retro: '复盘分析' }

export default function ModelSettings({ open, settings, onClose, onChanged }) {
  const [draft, setDraft] = useState({})
  const [toast, setToast] = useState('')
  const [rec, setRec] = useState(null)
  const [recLoading, setRecLoading] = useState(false)
  const [previewVoice, setPreviewVoice] = useState('')
  const [saving, setSaving] = useState(false)
  const toastTimer = useRef(null)
  const audioRef = useRef(null)

  // 每次打开：从最新 settings 同步草稿
  useEffect(() => {
    if (!open || !settings) return
    setDraft({
      llm_backend: settings.llm?.manual_backend ?? 'auto',
      llm_model: settings.llm?.manual_model ?? '',
      tts_engine: settings.tts?.engine ?? 'auto',
      tts_voice: settings.tts?.voice ?? settings.tts?.default ?? '',
      asr_engine: settings.asr?.engine ?? 'auto',
      continuous_talk: !!(settings.voice && settings.voice.continuous_talk),
    })
    setRec(null)
    setToast('')
    if (audioRef.current) { try { audioRef.current.stop() } catch { /* ignore */ } audioRef.current = null }
  }, [open, settings])

  if (!open) return null

  const showToast = (msg) => {
    setToast(msg)
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(''), 3000)
  }

  const set = (k, v) => setDraft((d) => ({ ...d, [k]: v }))

  const save = async () => {
    setSaving(true)
    try {
      // llm_model 仅对 aliyun（或 auto 时手动选择 qwen）有意义；
      // deepseek/mock 后端不传，避免后端 qwen 模型白名单校验失败
      const llmModel = draft.llm_backend === 'deepseek' || draft.llm_backend === 'mock'
        ? undefined
        : draft.llm_model || undefined
      const payload = {
        llm_backend: draft.llm_backend,
        llm_model: llmModel,
        tts_engine: draft.tts_engine,
        tts_voice: draft.tts_voice || undefined,
        asr_engine: draft.asr_engine,
        continuous_talk: draft.continuous_talk,
      }
      await putSettings(payload)
      if (onChanged) onChanged()
      showToast('✅ 设置已保存')
    } catch (e) {
      showToast(e.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const applyRecommend = async () => {
    setRecLoading(true)
    setRec(null)
    try {
      const data = await recommendModel('__all__')
      setRec(data.scenes || {})
    } catch (e) {
      showToast(e.message || '推荐失败')
    } finally {
      setRecLoading(false)
    }
  }

  const applyRecModel = (s) => {
    const r = rec[s]
    if (!r) return
    setDraft((d) => ({ ...d, llm_backend: r.backend, llm_model: r.model || d.llm_model }))
    showToast(`已应用 ${SCENE_LABEL[s] || s} → ${r.model || r.backend}`)
  }

  const playPreview = async (voice) => {
    if (previewVoice === voice && audioRef.current) {
      try { audioRef.current.stop() } catch { /* ignore */ }
      audioRef.current = null
      setPreviewVoice('')
      return
    }
    setPreviewVoice(voice)
    try {
      const blob = await ttsPreview(voice)
      const handle = audio.playMp3Blob(blob)
      audioRef.current = handle
      handle.promise.then(() => { if (audioRef.current === handle) audioRef.current = null; setPreviewVoice('') })
    } catch (e) {
      setPreviewVoice('')
      showToast(e.message || '试听失败')
    }
  }

  const llmGroups = (settings && settings.llm && settings.llm.models) || []
  const keys = (settings && settings.keys) || {}
  const voices = (settings && settings.tts && settings.tts.voices) || []

  const currentLLMModel = draft.llm_model || (settings && settings.llm && settings.llm.model) || 'qwen-plus'

  return (
    <div className={`ms-drawer ${open ? 'show' : ''}`}>
      <div className="ms-drawer-head">
        <span className="ms-drawer-title">模型 · 语音设置</span>
        <button className="ms-drawer-close" onClick={onClose} title="关闭">✕</button>
      </div>

      <div className="ms-drawer-body">
        {/* ===== LLM ===== */}
        <section className="ms-sec">
          <div className="ms-sec-title">LLM 大脑</div>
          <div className="ms-field">
            <label>后端</label>
            <select value={draft.llm_backend} onChange={(e) => set('llm_backend', e.target.value)}>
              {Object.entries(BACKEND_LABEL).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div className="ms-field">
            <label>模型</label>
            <select
              value={draft.llm_backend === 'deepseek' ? 'deepseek-chat' : draft.llm_model}
              onChange={(e) => set('llm_model', e.target.value)}
              disabled={draft.llm_backend === 'mock' || draft.llm_backend === 'deepseek'}
            >
              {draft.llm_backend === 'deepseek'
                ? <option value="deepseek-chat">deepseek-chat（固定）</option>
                : llmGroups.filter((g) => g.backend === 'aliyun').flatMap((g) => g.models).map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              {draft.llm_backend !== 'deepseek' && draft.llm_model === '' && (
                <option value="">自动（{currentLLMModel}）</option>
              )}
            </select>
          </div>
          <div className="ms-rec">
            <button className="btn btn-sm" onClick={applyRecommend} disabled={recLoading}>
              {recLoading ? '推荐中…' : '✨ 按场景 AI 推荐'}
            </button>
            {rec && (
              <div className="ms-rec-list">
                {Object.entries(rec).map(([s, r]) => (
                  <button key={s} className="ms-rec-item" onClick={() => applyRecModel(s)}>
                    <span className="ms-rec-scene">{SCENE_LABEL[s] || s}</span>
                    <span className="mono">{r.model || r.backend}</span>
                    <span className="ms-rec-apply">应用</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* ===== TTS ===== */}
        <section className="ms-sec">
          <div className="ms-sec-title">语音合成 TTS</div>
          <div className="ms-field">
            <label>引擎</label>
            <select value={draft.tts_engine} onChange={(e) => set('tts_engine', e.target.value)}>
              {Object.entries(TTS_ENGINE_LABEL).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div className="ms-field">
            <label>音色（DashScope sambert）</label>
            <div className="ms-voice-list">
              {voices.map((v) => (
                <button
                  key={v}
                  className={`ms-voice ${draft.tts_voice === v ? 'active' : ''}`}
                  onClick={() => set('tts_voice', v)}
                >
                  <span className="mono">{v}</span>
                  <span
                    className={`ms-prev ${previewVoice === v ? 'playing' : ''}`}
                    role="button"
                    title={previewVoice === v ? '停止' : '试听'}
                    onClick={(e) => { e.stopPropagation(); playPreview(v) }}
                  >
                    {previewVoice === v ? '■' : '▶'}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </section>

        {/* ===== ASR ===== */}
        <section className="ms-sec">
          <div className="ms-sec-title">语音识别 ASR</div>
          <div className="ms-field">
            <label>引擎</label>
            <select value={draft.asr_engine} onChange={(e) => set('asr_engine', e.target.value)}>
              {Object.entries(ASR_ENGINE_LABEL).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div className="ms-toggle">
            <label>
              <input
                type="checkbox"
                checked={draft.continuous_talk}
                onChange={(e) => set('continuous_talk', e.target.checked)}
              />
              <span>连续对话模式（点一下持续聆听，VAD 自动断句）</span>
            </label>
          </div>
        </section>

        {/* ===== Key 状态 ===== */}
        <section className="ms-sec">
          <div className="ms-sec-title">API Key 状态</div>
          <div className="ms-key-list">
            {Object.entries({ dashscope: '阿里云百炼', deepseek: 'DeepSeek', volcano: '火山引擎' }).map(([k, label]) => {
              const kk = keys[k] || {}
              return (
                <div className={`ms-key ${kk.has ? 'has' : ''}`} key={k}>
                  <span className="ms-key-label">{label}</span>
                  <span className="mono ms-key-masked">{kk.has ? (kk.masked || '***') : '未配置'}</span>
                  <span className={`ms-key-dot ${kk.has ? 'on' : ''}`} />
                </div>
              )
            })}
          </div>
          <div className="ms-hint">Key 仅存于服务端 .env，前端只显示脱敏尾号。</div>
        </section>
      </div>

      <div className="ms-drawer-foot">
        {toast && <span className="ms-toast">{toast}</span>}
        <button className="btn btn-sm" onClick={onClose}>取消</button>
        <button className="btn primary btn-sm" onClick={save} disabled={saving}>
          {saving ? '保存中…' : '保存设置'}
        </button>
      </div>
    </div>
  )
}
