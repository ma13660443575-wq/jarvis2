// ConversationOverlay — 玻璃拟态对话浮层（默认隐藏，交互时浮现）
// 空态仅极简提示，不展示任何功能列表；消息/命令确认/记忆候选/错误时浮现。
// 无发送按钮——文字输入 Enter 发送；语音为主交互。
// 整改：Markdown 渲染（renderMd / renderPlain，页面零裸 md 符号）、[[wikilink]] 可点击、
//       来源芯片、四角 L 角标、流光边框。
import { useEffect } from 'react'
import MemoryCandidateCard from './MemoryCandidateCard'
import { renderMd, renderPlain } from '../lib/markdown'

const FileChipIcon = () => (
  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6" />
  </svg>
)

export default function ConversationOverlay({
  messages, busy, input, inputRef, draftHint = false, onInput, onSend,
  err, cands, onCand, cmd, plan, onConfirmRun, onRejectRun,
  onConfirmAction, onRejectAction,
  onConfirmTaskStep, onRejectTaskStep,
  active, docked = false, onActivate, onOpenFile,
}) {
  const hasMsg = messages.length > 0
  const computerAction = !!(plan && String(plan.capability_id || '').startsWith('computer.'))
  const desktopTask = !!(plan && plan.desktop_task_owned === true && plan.task_type === 'desktop_task')
  const riskLabel = plan?.risk === 'high' ? '高风险' : plan?.risk === 'medium' ? '中风险' : '低风险'

  useEffect(() => {
    if (active && !docked && inputRef?.current) inputRef.current.focus()
  }, [active, docked, inputRef])

  // 点击消息内的 [[wikilink]] 芯片 → 打开对应文件（事件委托，规避 DOMPurify 过滤）
  const onMsgClick = (e) => {
    const wl = e.target && e.target.closest && e.target.closest('.wl')
    if (wl && wl.dataset && wl.dataset.path) onOpenFile && onOpenFile(wl.dataset.path)
  }

  // 收起（沉浸模式）时整体隐藏，让位给粒子核心
  const show = !docked && (active || hasMsg || !!cmd || !!plan || (cands && cands.length > 0) || !!err)

  return (
    <>
      {/* 空态唤起提示：只有一行极淡文字，点击浮现输入（沉浸模式且无消息时也展示） */}
      {!show && !hasMsg && (
        <button className="start-hint" onClick={onActivate} disabled={busy}>
          <span className="start-hint-dot" /> 点击屏幕开始
        </button>
      )}

      <div className={`overlay glass ${hasMsg ? 'has-msg' : ''} ${show ? 'show' : ''}`}>
        {/* 四角 L 型角标（配合流光边框） */}
        <span className="corner tl" />
        <span className="corner tr" />
        <span className="corner bl" />
        <span className="corner br" />

        {/* 空态：一句话，不列功能 */}
        {!hasMsg && (
          <div className="overlay-empty">
            <div className="jarvis-greeting">你好，我是 JARVIS</div>
            <div className="jarvis-sub">按住下方光环说话，或在这里输入</div>
          </div>
        )}

        {/* 消息列表 */}
        {hasMsg && (
          <div className="overlay-messages" onClick={onMsgClick}>
            {messages.map((m, i) => {
              const last = i === messages.length - 1
              const isUser = m.role === 'user'
              const dim = !last && i < messages.length - 3
              const streaming = !!m.streaming
              // 流式：纯文本（彻底零裸符号）；完成：完整 Markdown（含可点击 wikilink）
              const html = m.content
                ? (streaming ? renderPlain(m.content) : renderMd(m.content))
                : ''
              return (
                <div key={i} className={`msg ${isUser ? 'user' : 'assistant'} ${last ? 'current' : ''} ${dim ? 'dim' : ''}`}>
                  <div
                    className={`msg-content ${streaming ? 'plain' : ''}`}
                    dangerouslySetInnerHTML={{ __html: html }}
                  />
                  {streaming && !m.content && <span className="spin" />}
                  {streaming && m.content && <span className="cursor" />}
                  {m.sources && m.sources.length > 0 && (
                    <div className="msg-sources">
                      {m.sources.slice(0, 4).map((s) => (
                        <button
                          key={s}
                          className="src-chip"
                          onClick={() => onOpenFile && onOpenFile(s)}
                          title={`点击预览 ${s}`}
                        >
                          <FileChipIcon />
                          <span>{s}</span>
                        </button>
                      ))}
                      {m.sources.length > 4 && (
                        <span className="src-chip more">+{m.sources.length - 4}</span>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {/* Agent 能力计划：把“将要做什么”显式呈现给用户，避免黑盒执行 */}
        {plan && plan.mode === 'command' && (
          <div className={`agent-plan-card plan-${plan.status || 'planned'}`}>
            <div className="agent-plan-kicker">{desktopTask ? 'DESKTOP TASK' : 'ACTION PLAN'} · {String(plan.status || 'planned').toUpperCase()}</div>
            <div className="agent-plan-title">{plan.title || '能力计划'}</div>
            <div className="agent-plan-desc">{plan.description}</div>
            <div className="agent-plan-meta">
              <span>{plan.requires_confirmation ? (desktopTask || computerAction ? '逐步确认操作' : '需确认写入') : '只读执行'}</span>
              <span>{plan.steps?.length || 0} 个步骤</span>
              <span>{riskLabel}</span>
            </div>
            {computerAction && plan.target && (
              <div className="agent-plan-target" title={plan.target}>{plan.target}</div>
            )}
            {desktopTask && plan.goal && (
              <div className="agent-plan-target">目标：{plan.goal}</div>
            )}
            {plan.steps?.length > 0 && (
              <div className="agent-plan-steps">
                {plan.steps.map((step, i) => (
                  <div key={`${step.capability_id || 'step'}-${i}`} className="agent-plan-step">
                    <span className="agent-plan-index">{String(i + 1).padStart(2, '0')}</span>
                    <span>{step.title || step.capability_id}</span>
                    <span>{String(step.status || 'PENDING').toLowerCase()}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="agent-plan-note">{plan.confirmation_reason}</div>
            {plan.status === 'failed' && (plan.error_code || plan.error) && (
              <div className="agent-plan-note">
                {plan.error_code ? `[${plan.error_code}] ` : ''}{plan.error || '本机动作失败'}
              </div>
            )}
            {plan.save_state === 'SAVE_AS_REQUIRED' && (
              <div className="agent-plan-note">已触发保存，当前需要选择保存位置。</div>
            )}
            {(computerAction || desktopTask) && plan.status === 'waiting_confirmation' && plan.action_id && (
              <div className="agent-plan-actions">
                <button
                  className="btn primary btn-sm"
                  onClick={() => desktopTask
                    ? onConfirmTaskStep && onConfirmTaskStep(plan.task_id, plan.current_step_id, plan.action_id)
                    : onConfirmAction && onConfirmAction(plan.action_id)}
                >{plan.confirmation_label || '确认执行'}</button>
                <button
                  className="btn btn-sm"
                  onClick={() => desktopTask
                    ? onRejectTaskStep && onRejectTaskStep(plan.task_id, plan.current_step_id, plan.action_id)
                    : onRejectAction && onRejectAction(plan.action_id)}
                >取消</button>
              </div>
            )}
          </div>
        )}

        {/* 命令草稿确认卡 */}
        {cmd && cmd.status === 'draft' && (
          <div className="cmd-card">
            <div className="cmd-card-title">工作流执行完成，以下内容待你确认</div>
            {(cmd.drafts || []).map((d, i) => (
              <div key={i} className="cmd-file mono">· {d.rel_path || d.path || d}</div>
            ))}
            <div className="cmd-actions">
              <button className="btn primary btn-sm" onClick={() => onConfirmRun(cmd.run_id)}>确认写入</button>
              <button className="btn btn-sm" onClick={() => onRejectRun(cmd.run_id)}>拒绝</button>
            </div>
          </div>
        )}

        {/* 记忆候选 */}
        <MemoryCandidateCard cands={cands} onAct={onCand} />

        {/* 错误提示 */}
        {err && <div className="err-tip">⚠ {err}</div>}

        {/* 语音识别草稿提示：让用户知道要改好后回车发送（而非自动发出） */}
        {draftHint && <div className="draft-hint">✨ 已识别，改好后按 ↵ 发送</div>}

        {/* 输入区：无发送按钮，Enter 发送 */}
        <div className="input-row">
          <input
            ref={inputRef}
            className="input"
            value={input}
            onChange={(e) => onInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend() } }}
            onFocus={() => onActivate()}
            placeholder={busy ? 'JARVIS 正在思考…' : '和 JARVIS 说点什么…'}
          />
        </div>
      </div>
    </>
  )
}
