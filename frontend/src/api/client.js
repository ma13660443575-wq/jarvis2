// JARVIS2 · API 客户端（fetch 封装）

const BASE = ''; // 同源：dev 走 vite proxy，prod 由 FastAPI 挂载

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = `请求失败 (${res.status})`;
    let errorCode = '';
    let payload = null;
    try {
      payload = await res.json();
      const bodyDetail = payload && payload.detail;
      const data = payload && payload.data;
      const actionPlan = data && data.action_plan;
      const errorBody = actionPlan || data || bodyDetail;
      if (typeof errorBody === 'string') detail = errorBody;
      else if (errorBody && typeof errorBody === 'object') {
        detail = errorBody.error || errorBody.message || errorBody.error_code || detail;
        errorCode = errorBody.error_code || '';
      }
    } catch { /* ignore */ }
    const error = new Error(detail);
    if (errorCode) error.code = errorCode;
    error.payload = payload;
    throw error;
  }
  return res.json();
}

export const api = {
  get: (path) => request(path),
  post: (path, data) => request(path, { method: 'POST', body: JSON.stringify(data || {}) }),
};

/** 运行状态：vault 统计 + 契约自检 */
export async function getStatus() {
  const { data } = await request('/api/status');
  return data;
}

/** 健康检查：ai_mode / llm / vector 能力 */
export async function getHealth() {
  const { data } = await request('/api/health');
  return data;
}

/** 公开配置：唤醒词等（唤醒 agent 接入后仅作兼容保留） */
export async function getConfigPublic() {
  const { data } = await request('/api/config/public');
  return data;
}

// ---- 全局后台唤醒 agent ----
/** 唤醒 agent 状态（转发 127.0.0.1:8123；离线返回 state=offline） */
export async function getWakeStatus() {
  const { data } = await request('/api/wake/status');
  return data;
}

/** 开/关唤醒 agent 常驻监听 */
export async function postWakeToggle(enabled) {
  const { data } = await request('/api/wake/toggle', {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  });
  return data;
}

/** 暂停/恢复 agent 监听（PTT 录音期间调用，防双麦冲突） */
export async function postWakePaused(v) {
  await request(v ? '/api/wake/pause' : '/api/wake/resume', { method: 'POST', body: '{}' });
}

/** 上报 HUD 窗口可见状态（决定唤醒回复由前端朗读还是 agent 代读） */
export async function postWakeHud(open) {
  await request('/api/wake/hud', { method: 'POST', body: JSON.stringify({ open }) });
}

export async function getDashboard() {
  const { data } = await request('/api/dashboard');
  return data;
}

export async function getBriefing() {
  const { data } = await request('/api/briefing');
  return data;
}

// ---- 运行洞察（健康度 + token 消耗 + 改进建议）----
export async function getInsight(days = 7) {
  const { data } = await request(`/api/insight?days=${days}`);
  return data;
}

export async function ping() {
  const { data } = await request('/api/ping');
  return data;
}

// ---- 技能 ----
export async function getSkills() {
  const { data } = await request('/api/skills');
  return data;
}

export async function getSkill(slug) {
  const { data } = await request(`/api/skills/${slug}`);
  return data;
}

// ---- 复习 ----
export async function getReviewToday() {
  const { data } = await request('/api/review/today');
  return data;
}

export async function gradeCard(relPath, quality) {
  const { data } = await request('/api/review/grade', {
    method: 'POST',
    body: JSON.stringify({ rel_path: relPath, quality }),
  });
  return data;
}

// ---- 任务 ----
export async function getTasks() {
  const { data } = await request('/api/tasks');
  return data;
}

export async function createTask(title, priority = '中', project = null) {
  const { data } = await request('/api/tasks', {
    method: 'POST',
    body: JSON.stringify({ title, priority, project }),
  });
  return data;
}

export async function setTaskStatus(id, status) {
  const { data } = await request(`/api/tasks/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  });
  return data;
}

export async function deleteTask(id) {
  return request(`/api/tasks/${id}`, { method: 'DELETE' });
}

// ---- 命令台 / 工作流 ----
export async function getWorkflows() {
  const { data } = await request('/api/workflows');
  return data;
}

export async function executeCommand(text, payload = {}) {
  const { data } = await request('/api/command/execute', {
    method: 'POST',
    body: JSON.stringify({ text, payload }),
  });
  return data;
}

/** Agent 能力目录（只返回实际注册能力，不包含未实现的桌面控制）。 */
export async function getCapabilities() {
  const { data } = await request('/api/capabilities');
  return data;
}

/** 预览输入对应的能力计划；只规划，不执行。 */
export async function planAgent(text, payload = {}) {
  const { data } = await request('/api/agent/plan', {
    method: 'POST',
    body: JSON.stringify({ text, payload }),
  });
  return data;
}

// ---- 本地电脑观察桥 + 受确认保护的动作 ----
export async function getComputerStatus() {
  const { data } = await request('/api/computer/status');
  return data;
}

export async function getComputerWindows(limit = 20) {
  const { data } = await request(`/api/computer/windows?limit=${limit}`);
  return data;
}

export async function getComputerUi(maxDepth = 2, maxNodes = 80) {
  const { data } = await request(`/api/computer/ui?max_depth=${maxDepth}&max_nodes=${maxNodes}`);
  return data;
}

/** 确认一个短时有效的本地电脑动作。 */
export async function confirmComputerAction(actionId) {
  const { data } = await request(`/api/computer/actions/${encodeURIComponent(actionId)}/confirm`, {
    method: 'POST',
    body: '{}',
  });
  return data;
}

/** 拒绝并销毁一个待确认的本地电脑动作。 */
export async function rejectComputerAction(actionId) {
  const { data } = await request(`/api/computer/actions/${encodeURIComponent(actionId)}/reject`, {
    method: 'POST',
    body: '{}',
  });
  return data;
}

/** 读取 Desktop Task 的安全运行状态。 */
export async function getDesktopTask(taskId) {
  const { data } = await request(`/api/agent/tasks/${encodeURIComponent(taskId)}`);
  return data;
}

/** 使用 task_id + step_id + action_id 三元绑定确认 TaskStep。 */
export async function confirmDesktopTaskStep(taskId, stepId, actionId) {
  const { data } = await request(
    `/api/agent/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/confirm`,
    { method: 'POST', body: JSON.stringify({ action_id: actionId }) },
  );
  return data;
}

/** 使用 task_id + step_id + action_id 三元绑定拒绝 TaskStep。 */
export async function rejectDesktopTaskStep(taskId, stepId, actionId) {
  const { data } = await request(
    `/api/agent/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/reject`,
    { method: 'POST', body: JSON.stringify({ action_id: actionId }) },
  );
  return data;
}

export async function getRuns(status = null) {
  const q = status ? `?status=${encodeURIComponent(status)}` : '';
  const { data } = await request(`/api/runs${q}`);
  return data;
}

export async function getRunDrafts(runId) {
  const { data } = await request(`/api/runs/${runId}/drafts`);
  return data;
}

export async function confirmRun(runId) {
  const { data } = await request(`/api/runs/${runId}/confirm`, { method: 'POST', body: '{}' });
  return data;
}

export async function rejectRun(runId) {
  const { data } = await request(`/api/runs/${runId}/reject`, { method: 'POST', body: '{}' });
  return data;
}

// ---- 知识库 ----
export async function getKnowledgeTree() {
  const { data } = await request('/api/knowledge/tree');
  return data;
}

// ---- 复盘 ----
export async function getRetro() {
  const { data } = await request('/api/retro');
  return data;
}

// ---- 全局搜索 ----
export async function searchNotes(q, limit = 10) {
  const { data } = await request(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  return data;
}

export async function getNote(path) {
  const { data } = await request(`/api/notes?path=${encodeURIComponent(path)}`);
  return data;
}

/** 记忆星图节点（向量库滚动拉取）：[{ rel_path, title }] */
export async function getMemoryNodes(limit = 120) {
  const { data } = await request(`/api/memory/nodes?limit=${limit}`);
  return data;
}

// ---- 感官 ----
export async function getSenses() {
  const { data } = await request('/api/senses');
  return data;
}

export async function senseCall(name, params = {}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null && v !== '') qs.set(k, v); });
  const q = qs.toString();
  const { data } = await request(`/api/senses/${name}${q ? `?${q}` : ''}`);
  return data;
}

// ---- SSE 最近事件（首次进入补状态）----
export async function getRecentEvents(channel = null, limit = 10) {
  const qs = new URLSearchParams();
  if (channel) qs.set('channel', channel);
  qs.set('limit', limit);
  const { data } = await request(`/api/events/recent?${qs.toString()}`);
  return data;
}

// ---- 语音（本地 sherpa-onnx ASR + Edge 神经 TTS）----
/** 语音识别：WAV Blob → 文本 */
export async function postVoiceAsr(blob) {
  const form = new FormData();
  form.append('audio', blob, 'voice.wav');
  const res = await fetch('/api/voice/asr', { method: 'POST', body: form });
  if (!res.ok) {
    let detail = '语音识别失败';
    try { const b = await res.json(); if (b.detail) detail = b.detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  const { data } = await res.json();
  return data.text;
}

/** 语音合成：文本 → mp3 Blob（voice 为 sambert 音色，可选；缺省走后端当前 TTS 链） */
export async function postVoiceTts(text, voice) {
  const res = await fetch('/api/voice/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice: voice || undefined }),
  });
  if (!res.ok) {
    let detail = '语音合成失败';
    try { const b = await res.json(); if (b.detail) detail = b.detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return await res.blob();
}

// ---- 模型 / 设置（DashScope 多后端 + sambert 音色 + 引擎切换）----
/** 全量设置（key 脱敏；llm / tts / asr / voice 分组） */
export async function getSettings() {
  const { data } = await request('/api/settings');
  return data;
}

/** 批量保存设置：llm_backend / llm_model / tts_engine / tts_voice / asr_engine / continuous_talk */
export async function putSettings(payload) {
  const { data } = await request('/api/settings', {
    method: 'PUT',
    body: JSON.stringify(payload || {}),
  });
  return data;
}

/** 三类模型列表：llm / tts / asr */
export async function getModels() {
  const { data } = await request('/api/models');
  return data;
}

/** 试听指定 sambert 音色 → mp3 Blob */
export async function ttsPreview(voice, text) {
  const res = await fetch('/api/settings/tts/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ voice, text: text || undefined }),
  });
  if (!res.ok) {
    let detail = '试听失败';
    try { const b = await res.json(); if (b.detail) detail = b.detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return await res.blob();
}

/** 按场景返回 AI 推荐模型：{scene, backend, model, reason} 或 {scenes:{...}} */
export async function recommendModel(scene = 'chat') {
  const { data } = await request('/api/settings/recommend', {
    method: 'POST',
    body: JSON.stringify({ scene }),
  });
  return data;
}
