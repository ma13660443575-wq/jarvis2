# JARVIS2 项目状态

更新时间：2026-09-05

## 当前阶段

第四阶段 4A 的实现切片已完成代码级闭环，等待评审后再做真实 Windows Notepad 联调。当前不应将其描述为完整 Desktop Task Agent，也不应视为第四阶段全部完成。

## 4A 已实现

- `backend/agent/task_models.py`：Task、TaskStep、TaskResult、TaskContext 以及 Pydantic 结构校验模型。
- `backend/agent/task_state.py`：Task/Step 显式状态转换表。
- `backend/agent/task_store.py`：进程内 Task Store、跨 HTTP 请求生命周期、session 单活跃任务约束、action 绑定。
- `backend/agent/task_planner.py`：最多 5 步的受约束 Structured Planner；当前只接受已打开记事本 → 输入引号正文 → 点击保存。
- `backend/agent/task_engine.py`：逐步准备、确认、执行、验证、结果归档和状态推进；正文只存在运行时 `TaskContext`。
- `backend/routers/agent.py`：Task 查询及按 `task_id + step_id + action_id` 的确认/拒绝 API。
- `backend/routers/command.py`、`backend/brain/answer.py`、`backend/routers/wake.py`：Task 分流与结果返回。
- `backend/routers/computer.py`：阻止旧 Computer Action API 绕过 Task 三元绑定。
- `backend/computer/trace.py`：standalone action 与 Task Step 共用的结构化执行追踪；只记录执行元数据，不记录输入正文、剪贴板或密码。
- `backend/agent/broker.py`、`backend/agent/task_engine.py`：统一 execution owner；同一 session 的活动 Task 会阻止新的 standalone Computer Action。
- `frontend/src/api/client.js`、`frontend/src/pages/Core.jsx`、`frontend/src/components/ConversationOverlay.jsx`：最小计划展示、逐步确认/取消和状态反馈。

## 明确未实现

- `backend/computer/windows.py`、`backend/computer/actions.py` 仅增加 side-effect-free trace 埋点；未改变 ControlPath、Popup Resolver、Verify、超时、重试或执行动作核心逻辑。
- 未实现开放式循环、恢复/重试、跨应用、多任务并行、启动应用、Save As 自动填充、新 UIA 动作。
- Save timeout 根因尚未修复；本轮只增加 trace，等待真实 Windows standalone Save 与 Task Step Save 日志后再诊断。
- `Task Store` 当前为进程内存；后端重启会丢失未结束 Task。
- `WAITING_USER` 只返回人工选择保存位置的状态，不自动接管系统 Save As 对话框。
- 尚未进行真实 Windows Notepad 的人工端到端联调；应在评审通过后执行。

## 最近验证

- 4A/ownership/trace 新增测试 + 既有 Agent/Computer/UIA 测试：`74 passed`。
- 全量测试：`100 passed, 1 skipped, 1 failed`；唯一失败为本地 ASR 可用性环境条件（`sherpa_onnx` 被发现但 `asr_available()` 为 False），不属于本轮 Task 变更。
- 前端 `npm run build`：通过（94 modules）；Vite 仍提示现有大 chunk warning。
- 新增后端文件及相关路由已通过 Python 语法编译和应用路由装配检查。

## 当前联调边界

- 活动 Task 状态 `READY/RUNNING/WAITING_CONFIRMATION/WAITING_USER/PAUSED` 占用 session；新的 Desktop Task 返回 `TASK_ALREADY_ACTIVE`，新的 standalone Computer Action 返回 `DESKTOP_EXECUTION_BUSY`；普通聊天不受影响。
- trace 阶段覆盖 `PREPARE_START`、窗口/控件解析、确认、执行、ControlPath File/Expand、Popup baseline/poll/candidate、Save item/invoke、Verify 和最终 `RESULT`；用 `execution_trace_id` 关联 standalone 与 Task Step 日志。
- 仍未进入 4B；未新增 Desktop Action 能力；待用户提供真实 Windows 联调 trace 后再决定是否需要修改冻结核心。
