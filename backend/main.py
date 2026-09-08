# -*- coding: utf-8 -*-
"""
main.py — JARVIS2 FastAPI 入口
- 装配 routers（先注册 API 路由，后挂载前端静态目录，保证 /api/* 优先）。
- 启动时：初始化 data/ + SQLite；vault 路径契约自检（只警告，不自动建目录）。
- 前端由 Vite 构建到 frontend/dist，存在则同源挂载；开发期由 Vite dev server 代理。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from . import db
from .vault import paths

log = logging.getLogger("jarvis2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- 启动 ----
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()

    # vault 路径契约自检（只警告，绝不自动建目录污染 vault）
    contract = paths.check_contract()
    if contract["ok"]:
        log.info("[vault] 路径契约自检通过 ✅ vault=%s", config.VAULT_PATH)
    else:
        log.warning("[vault] 路径契约缺失目录 %s（不自动创建；技能库 06_Capability 由应用首次使用时创建）",
                    contract["missing"])

    # 后台预热（向量索引，P1 接入；失败静默降级关键词）
    try:
        import threading

        def _warm():
            try:
                from .memory import vector
                vector.ensure_index()
            except Exception as e:  # noqa: BLE001
                log.warning("[vector] 预热跳过: %s", e)
        threading.Thread(target=_warm, daemon=True).start()
    except Exception as e:  # noqa: BLE001
        log.warning("[vector] 预热未启动: %s", e)

    # 每日简报调度（启动懒生成当天简报 + 每天 08:30 重生成）
    try:
        from .heart import scheduler as heart_scheduler
        heart_scheduler.start()
    except Exception as e:  # noqa: BLE001
        log.warning("[heart] 调度器未启动: %s", e)

    # 向全局唤醒 agent 上报本后端端口（agent 8123 在线才生效；dev/frozen 均覆盖）
    try:
        import json
        import threading
        import urllib.request

        def _reg_agent():
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{config.WAKE_AGENT_PORT}/register",
                    data=json.dumps({"backend_port": config.PORT}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=1.5).read()
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=_reg_agent, daemon=True).start()
    except Exception:  # noqa: BLE001
        pass

    # 后台预下载本地 ASR 模型（本地引擎首启自动下载，不阻塞启动；
    # 就绪后 /api/health 的 asr_enabled=true → 前端语音按钮自动点亮）
    if config.VOICE_ENGINE == "local":
        try:
            import threading

            def _ensure_asr():
                try:
                    from .voice import local as voice_local
                    if not voice_local.asr_available():
                        voice_local.ensure_models()
                        log.info("[voice] 本地 ASR 模型就绪 ✅ %s", voice_local.model_dir())
                except Exception as e:  # noqa: BLE001
                    log.warning("[voice] ASR 模型预下载失败（可稍后重试，或设 VOICE_ENGINE=volcano）: %s", e)
            threading.Thread(target=_ensure_asr, daemon=True).start()
        except Exception:  # noqa: BLE001
            pass

    yield

    # ---- 关闭 ----
    try:
        from .heart import scheduler as heart_scheduler
        heart_scheduler.stop()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="JARVIS2", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/ping")
def _ping():
    return {"ok": True, "data": {"pong": True}}


# ===== API 路由 =====
from .routers import status as status_router  # noqa: E402
from .routers import chat as chat_router  # noqa: E402
from .routers import command as command_router  # noqa: E402
from .routers import app as app_router  # noqa: E402
from .routers import events as events_router  # noqa: E402
from .routers import search as search_router  # noqa: E402
from .routers import retro as retro_router  # noqa: E402
from .routers import senses as senses_router  # noqa: E402
from .routers import voice as voice_router  # noqa: E402
from .routers import wake as wake_router  # noqa: E402
from .routers import insight as insight_router  # noqa: E402
from .routers import settings as settings_router  # noqa: E402
from .routers import agent as agent_router  # noqa: E402
from .routers import computer as computer_router  # noqa: E402
app.include_router(status_router.router)
app.include_router(chat_router.router)
app.include_router(command_router.router)
app.include_router(app_router.router)
app.include_router(events_router.router)
app.include_router(search_router.router)
app.include_router(retro_router.router)
app.include_router(senses_router.router)
app.include_router(voice_router.router)
app.include_router(wake_router.router)
app.include_router(insight_router.router)
app.include_router(settings_router.router)
app.include_router(agent_router.router)
app.include_router(computer_router.router)


# ===== 前端静态挂载（build 产物存在时）=====
_frontend_dist = config.FRONTEND_DIST
if _frontend_dist.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_frontend_dist / "assets")),
        name="frontend-assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):
        """SPA 兜底：非 /api 路径一律返回 index.html（供 BrowserRouter 深链/刷新）。"""
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"ok": False, "detail": "not found"})
        return FileResponse(_frontend_dist / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=config.HOST, port=config.PORT)
