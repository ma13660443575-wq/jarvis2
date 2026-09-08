// VoiceButton — 极简呼吸光环（语音输入入口）
// 两种模式：
//   连续对话（continuous=true）：点一下开始持续聆听（VAD 自动断句），再点停止。
//   PTT（continuous=false）：按住说话，松开结束（默认）。
// 状态映射 useVoice：recording 蓝脉冲 / asr 转圈 / error 红点可点重试。
export default function VoiceButton({ enabled, state, continuous, onBegin, onEnd, onToggleContinuous, onReset }) {
  const busy = state === 'asr'
  const recording = state === 'recording'
  const error = state === 'error'

  const handleDown = (e) => {
    if (!enabled || busy || error || continuous) return
    e.preventDefault()
    onBegin()
  }
  const handleUp = () => {
    if (!continuous && recording) onEnd()
  }
  const handleClick = () => {
    if (error) { onReset && onReset(); return }
    if (!enabled) return
    if (continuous) { onToggleContinuous && onToggleContinuous(); return }
    // PTT 模式：轻点（无按住）也忽略，避免误触发
  }

  const tipText = !enabled
    ? '语音引擎未就绪：本地模型首次启动会自动下载，请稍候'
    : error
      ? '重试'
      : continuous
        ? recording
          ? '聆听中 · 再点结束'
          : '开始连续对话'
        : recording
          ? '松开结束'
          : '按住说话'

  return (
    <button
      className={`voice-halo state-${state} ${recording ? 'recording' : ''} ${!enabled ? 'disabled' : ''}`}
      disabled={!enabled && !error}
      onPointerDown={handleDown}
      onPointerUp={handleUp}
      onPointerLeave={handleUp}
      onClick={handleClick}
      title={tipText}
      aria-label={tipText}
    >
      <span className="voice-halo-ring" />
      <span className="voice-halo-dot" />
      <span className="voice-halo-tip">
        {error ? '重试' : recording ? (continuous ? '聆听中 · 再点结束' : '松开结束') : continuous ? '连续对话' : '按住说话'}
      </span>
    </button>
  )
}
