// lib/asrStream.js — DashScope 流式 ASR 前端通道（WebSocket 直传 PCM）
// ============================================================
// 协议（与 backend/routers/voice.py 的 /api/voice/asr/stream 对应）：
//   连上 → 发 {type:"config", sample_rate:16000} → 之后可发二进制 PCM 帧
//   服务端整句就绪 → 推 {type:"final", text}（连续对话：一条一句，实时回显）
//   前端发 {type:"stop"} → 服务端 finalize → 推 {type:"done", text} → 关闭
//   异常 → {type:"error", message}
// 断连/异常统一走 onError，由调用方降级到一次性 postVoiceAsr（本地 sherpa 兜底）。

/**
 * 建立流式 ASR 会话。
 * @param {Object} opts
 * @param {number} [opts.sampleRate] 采样率（默认 16000）
 * @param {(text:string)=>void} [opts.onFinal] 每识别出一句整句回调
 * @param {(text:string)=>void} [opts.onDone] 会话结束（stop）时回调
 * @param {(message:string)=>void} [opts.onError] 连接错误/断流回调
 * @returns {{ sendPcm:(Int16Array)=>void, stop:()=>void, close:()=>void, ready:boolean }}
 */
export function createAsrStream({ sampleRate = 16000, onFinal, onDone, onError } = {}) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${window.location.host}/api/voice/asr/stream`
  const pending = [] // WS 连接建立前暂存 PCM 帧

  let ws
  let closed = false

  const fireError = (msg) => {
    if (closed) return
    closed = true
    try { onError && onError(msg) } catch { /* ignore */ }
  }

  try {
    ws = new WebSocket(url)
  } catch (e) {
    fireError(e.message || '无法建立语音识别连接')
    return { sendPcm() {}, stop() {}, close() {}, ready: false, failed: true }
  }
  ws.binaryType = 'arraybuffer'

  ws.onopen = () => {
    try {
      ws.send(JSON.stringify({ type: 'config', sample_rate: sampleRate }))
    } catch { /* ignore */ }
    while (pending.length) {
      const buf = pending.shift()
      if (ws.readyState === WebSocket.OPEN) ws.send(buf)
    }
  }

  ws.onmessage = (e) => {
    let msg
    try { msg = JSON.parse(e.data) } catch { return }
    if (msg.type === 'final' && msg.text) {
      try { onFinal && onFinal(msg.text) } catch { /* ignore */ }
    } else if (msg.type === 'done') {
      closed = true
      try { onDone && onDone(msg.text || '') } catch { /* ignore */ }
    } else if (msg.type === 'error') {
      fireError(msg.message || '语音识别出错')
    }
  }

  ws.onerror = () => fireError('语音识别连接异常，请稍后再试')
  ws.onclose = () => { if (!closed) fireError('语音识别连接已断开') }

  return {
    failed: false,
    /** 推一帧 16kHz mono PCM16（Int16Array） */
    sendPcm(pcm) {
      if (!pcm || !pcm.length || !ws) return
      let buf
      try {
        buf = pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength)
      } catch { return }
      if (ws.readyState === WebSocket.OPEN) { try { ws.send(buf) } catch { /* ignore */ } }
      else if (ws.readyState === WebSocket.CONNECTING) pending.push(buf)
    },
    /** 通知服务端收尾（等待 done 后由 close 关闭） */
    stop() {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: 'stop' })) } catch { /* ignore */ }
      }
    },
    /** 强制关闭连接（幂等） */
    close() {
      closed = true
      try { if (ws) ws.close() } catch { /* ignore */ }
    },
    get ready() { return !!(ws && ws.readyState === WebSocket.OPEN) },
  }
}
