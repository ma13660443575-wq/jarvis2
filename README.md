# JARVIS2

JARVIS2 是一个本地优先的个人 AI 助手项目，包含 FastAPI 后端、React/Vite 前端，以及尚在开发中的 Tauri 桌面端。

项目目前只发布源码，桌面端尚未完成最终封装，未提交任何 `.exe` 或其他打包产物。

## 本地运行

1. 复制 `.env.example` 为 `.env`，按需填写 API Key 和本地知识库路径。
2. 安装 Python 依赖：

   ```powershell
   pip install -r requirements.txt
   ```

3. 启动后端：

   ```powershell
   python -m uvicorn backend.main:app --host 127.0.0.1 --port 8100
   ```

4. 启动前端：

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

## 隐私与发布边界

- API Key 只放在本地 `.env`，不会提交到仓库。
- `data/`、本地数据库、知识库内容、模型权重、依赖目录和构建目录均不提交。
- 桌面端 exe 等打包产物暂不发布，待项目完成后再单独规划发布方式。
