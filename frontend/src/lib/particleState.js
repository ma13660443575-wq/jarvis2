// 粒子共享状态
// R3F 在 useFrame 里每帧读取；React 组件写入。避免 R3F 与 React state 重渲染抖动。
export const particleState = {
  /** idle | listening | thinking | speaking | executing | confirming | error | memory */
  mode: 'idle',
  /** 0..1 音量强度（录音 Rms / 说话 FFT 均值） */
  intensity: 0,
  /** 64 频段 FFT 归一化幅值（speaking 时驱动粒子声波同步） */
  levels: new Float32Array(64),
  /** 状态文案（顶部提示） */
  statusText: '',
}
