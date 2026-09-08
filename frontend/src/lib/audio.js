// 音频采集与播放工具
// 采集：AudioContext 直采 PCM → 降采样到 16kHz → 封装标准 WAV（火山 ASR 要求 PCM16）
// 播放：AudioContext + createMediaElementSource 接 AnalyserNode，供粒子读 FFT 波形

function writeString(dv, offset, str) {
  for (let i = 0; i < str.length; i++) dv.setUint8(offset + i, str.charCodeAt(i));
}

/**
 * 创建 16kHz 降采样器（供唤醒词/录音等流式场景复用）。
 * 输入任意采样率的 Float32 帧，输出 Int16@16kHz；跨帧小数余量累加，避免逐帧独立降采样的漂移。
 * 用法：const ds = createDownsampler16k(ctx.sampleRate); ds(frame1); ds(frame2)...
 */
export function createDownsampler16k(rate) {
  const ratio = rate / 16000;
  let pending = new Float32Array(0);
  return function (chunk) {
    const merged = new Float32Array(pending.length + chunk.length);
    merged.set(pending, 0);
    merged.set(chunk, pending.length);
    const outLen = Math.floor(merged.length / ratio);
    const out = new Int16Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const srcIdx = Math.floor(i * ratio);
      let v = merged[srcIdx];
      v = Math.max(-1, Math.min(1, v));
      out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    pending = merged.slice(Math.floor(outLen * ratio)); // 保留不足一个输出样本的尾部
    return out;
  };
}

/** 封装 Int16Array → 标准 WAV Blob（44 字节 RIFF 头） */
export function encodeWav(pcm16, rate) {
  const buf = new ArrayBuffer(44 + pcm16.length * 2);
  const dv = new DataView(buf);
  writeString(dv, 0, 'RIFF');
  dv.setUint32(4, 36 + pcm16.length * 2, true);
  writeString(dv, 8, 'WAVE');
  writeString(dv, 12, 'fmt ');
  dv.setUint32(16, 16, true);        // fmt chunk size
  dv.setUint16(20, 1, true);         // PCM
  dv.setUint16(22, 1, true);         // mono
  dv.setUint32(24, rate, true);
  dv.setUint32(28, rate * 2, true);  // byte rate
  dv.setUint16(32, 2, true);         // block align
  dv.setUint16(34, 16, true);        // bits per sample
  writeString(dv, 36, 'data');
  dv.setUint32(40, pcm16.length * 2, true);
  for (let i = 0; i < pcm16.length; i++) dv.setInt16(44 + i * 2, pcm16[i], true);
  return new Blob([buf], { type: 'audio/wav' });
}

/**
 * 开始录音。返回句柄；调用 stopCapture(handle) 结束。
 */
export async function startCapture() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  const source = ctx.createMediaStreamSource(stream);
  const sampleRate = ctx.sampleRate;
  const chunks = [];
  // ScriptProcessor 已废弃但仍唯一可靠的逐块直采通道；blockSize 4096
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  processor.onaudioprocess = (e) => {
    chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  };
  source.connect(processor);
  processor.connect(ctx.destination);
  return { ctx, source, processor, stream, chunks, sampleRate };
}

/**
 * 结束录音 → 16kHz mono PCM16 WAV Blob。
 */
export function stopCapture(handle) {
  const { ctx, source, processor, stream, chunks, sampleRate } = handle;
  try { source.disconnect(); } catch { /* ignore */ }
  try { processor.disconnect(); } catch { /* ignore */ }
  stream.getTracks().forEach((t) => t.stop());
  try { ctx.close(); } catch { /* ignore */ }

  let total = 0;
  for (const c of chunks) total += c.length;
  if (total === 0) return null;
  const merged = new Float32Array(total);
  let off = 0;
  for (const c of chunks) { merged.set(c, off); off += c.length; }

  // 线性降采样到 16000
  const targetRate = 16000;
  const outLen = Math.max(1, Math.floor((merged.length * targetRate) / sampleRate));
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const srcIdx = Math.floor((i * sampleRate) / targetRate);
    let v = merged[Math.min(srcIdx, merged.length - 1)];
    v = Math.max(-1, Math.min(1, v));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return encodeWav(out, targetRate);
}

/**
 * 开始流式录音（连续对话）：持续把 16kHz mono PCM16 帧推给 onPcm，同时累积原始帧供兜底 WAV 导出。
 * 返回 { ctx, source, processor, stream, stop, toWav }。
 *  - onPcm(pcm: Int16Array)：每帧实时回调（降采样到 16k），供 WebSocket 直传 DashScope ASR。
 *  - toWav()：把本次已录内容封装为 16k WAV Blob（WS 断流时降级一次性识别用）。
 *  - stop()：停止采集并释放麦克风/AudioContext。
 */
export async function startCaptureStream(onPcm) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  const source = ctx.createMediaStreamSource(stream);
  const ds = createDownsampler16k(ctx.sampleRate);
  const rawChunks = [];
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  processor.onaudioprocess = (e) => {
    const frame = new Float32Array(e.inputBuffer.getChannelData(0));
    rawChunks.push(frame);
    const pcm = ds(frame);
    if (pcm.length && onPcm) { try { onPcm(pcm); } catch { /* ignore */ } }
  };
  source.connect(processor);
  processor.connect(ctx.destination);

  const stop = () => {
    try { source.disconnect(); } catch { /* ignore */ }
    try { processor.disconnect(); } catch { /* ignore */ }
    stream.getTracks().forEach((t) => t.stop());
    try { ctx.close(); } catch { /* ignore */ }
  };

  const toWav = () => {
    let total = 0;
    for (const c of rawChunks) total += c.length;
    if (!total) return null;
    const merged = new Float32Array(total);
    let off = 0;
    for (const c of rawChunks) { merged.set(c, off); off += c.length; }
    const targetRate = 16000;
    const outLen = Math.max(1, Math.floor((merged.length * targetRate) / ctx.sampleRate));
    const out = new Int16Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const srcIdx = Math.floor((i * ctx.sampleRate) / targetRate);
      let v = merged[Math.min(srcIdx, merged.length - 1)];
      v = Math.max(-1, Math.min(1, v));
      out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    return encodeWav(out, targetRate);
  };

  return { ctx, source, processor, stream, stop, toWav };
}

/**
 * 播放 mp3 Blob，返回 { analyser, promise, stop }。
 * promise 在播放自然结束或被 stop() 打断时 resolve；通过 analyser 读取 FFT 驱动粒子。
 * stop() 用于 Barge-in：立即停止播放并释放资源。
 */
export function playMp3Blob(blob) {
  const url = URL.createObjectURL(blob);
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  const audio = new Audio(url);
  audio.crossOrigin = 'anonymous';
  const source = ctx.createMediaElementSource(audio);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 128;
  source.connect(analyser);
  analyser.connect(ctx.destination);

  let resolvePromise;
  const promise = new Promise((resolve) => { resolvePromise = resolve; });

  let finished = false;
  const cleanup = () => {
    if (finished) return;
    finished = true;
    URL.revokeObjectURL(url);
    try { ctx.close(); } catch { /* ignore */ }
    resolvePromise();
  };

  audio.onended = cleanup;
  audio.onerror = cleanup;

  /** 打断：停止播放并释放资源（promise 立即 resolve）。 */
  const stop = () => {
    try { audio.pause(); } catch { /* ignore */ }
    try { audio.removeAttribute('src'); audio.load(); } catch { /* ignore */ }
    cleanup();
  };

  audio.play().catch(cleanup);
  return { analyser, promise, audio, stop };
}
