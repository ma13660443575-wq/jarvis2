// WakeToggle — 常驻聆听开关（光环左侧「耳朵」小圆钮）
// 点亮 = 常驻聆听中（gold 呼吸）；loading = 引擎加载旋转；error = 红点 + 原因气泡。
// 状态来源为全局后台唤醒 agent（useWake 轮询 /api/wake/status）；agent 离线 → 置灰不可点。
// 交互文案全用人话：说「JARVIS」唤起、正在听指令…，不让用户理解唤醒词/VAD 等术语。
const Ear = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 8.5a6 6 0 1 1 12 0c0 3-1.5 4.5-3 6-.9.9-1 2-1 3.5" />
    <path d="M14 18.5a1 1 0 1 1-4 0" />
  </svg>
)

const STATE_TIP = {
  offline: '唤醒服务未运行',
  loading: '唤醒服务连接中…',
  listening: '常驻聆听中 · 说「JARVIS」',
  armed: '已听到 JARVIS，请说出指令',
  recording: '正在听指令…',
  asr: '正在识别指令…',
  executing: '正在执行指令…',
}

export default function WakeToggle({ enabled, on, state, error, onToggle }) {
  const busy = state === 'loading'
  const tip = on
    ? (STATE_TIP[state] || '常驻聆听中')
    : '常驻聆听：说「JARVIS」唤起'

  return (
    <button
      className={`wake-toggle ${on ? 'on' : ''} ${busy ? 'loading' : ''} ${error ? 'error' : ''} ${!enabled ? 'disabled' : ''}`}
      onClick={() => enabled && onToggle()}
      disabled={!enabled}
      title={enabled ? (error || tip) : '唤醒服务未运行（需安装唤醒 agent）'}
      aria-label="常驻聆听开关"
      aria-pressed={!!on}
    >
      <Ear />
      {error && <span className="wake-toggle-dot" />}
      {error ? (
        <span className="wake-toggle-note">{error}</span>
      ) : (
        <span className="wake-toggle-tip">{tip}</span>
      )}
    </button>
  )
}
