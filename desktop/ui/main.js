/* main.js — JARVIS2 透明桌宠页逻辑
 *
 * 职责：
 *   1. 经 Tauri IPC 拿后端端口 → SSE /api/events 收 chat 事件
 *   2. chat.state=start → 说话辉光（窗口放大留白）；state=done → 气泡显示回复（6s 后收起）
 *   3. 拖动 vs 点击：>4px 进 OS 拖拽循环，松手存位置；未移动 → 唤起 HUD
 *   4. 跟随鼠标：窗口内 pointermove 微转动/微位移，pointerleave 复位
 *   5. 右键菜单：打开 HUD / 退出 JARVIS
 *
 * 纯浏览器预览（无 __TAURI__）时优雅降级：UI 可看、交互不可用。
 */
(function () {
  "use strict";

  const TAURI = window.__TAURI__;
  const $ = (id) => document.getElementById(id);
  const glow = $("glow");
  const body = $("body");
  const head = $("head");
  const bubble = $("bubble");
  const bubbleText = $("bubble-text");
  const ctx = $("ctx");

  /* ---------- 窗口尺寸（逻辑像素，与 tauri.conf.json 一致） ---------- */
  const IDLE = { w: 90, h: 149 };
  const BUBBLE = { w: 208, h: 272 };

  let bubbleTimer = null;

  /* ---------- 工具 ---------- */

  async function resizeTo(size) {
    if (!TAURI) return;
    try {
      const { window: winApi } = TAURI;
      const win = winApi.getCurrentWindow();
      const pos = await win.outerPosition();
      const cur = await win.outerSize();
      const scale = await win.scaleFactor();
      const newH = Math.round(size.h * scale);
      await win.setSize(new winApi.LogicalSize(size.w, size.h));
      // 保持底边锚定：高度变化后同步上移/下移，桌宠不跳（统一物理像素）
      await win.setPosition(
        new winApi.PhysicalPosition(pos.x, pos.y + cur.height - newH)
      );
    } catch (err) {
      console.warn("resize failed", err);
    }
  }

  function cleanMarkdown(s) {
    return s
      .replace(/```[\s\S]*?```/g, " [代码块] ")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/(\*\*|__|[*_~]{1,2})([^*_~\n]+)\1/g, "$2")
      .replace(/^\s*[-*+]\s+/gm, "· ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function clipText(s, max) {
    if (s.length <= max) return s;
    return s.slice(0, max) + "…";
  }

  /* ---------- 气泡 ---------- */

  function showBubble(rawText) {
    const text = clipText(cleanMarkdown(rawText), 160);
    if (!text) return;
    bubbleText.textContent = text;
    bubble.classList.remove("hidden");
    resizeTo(BUBBLE);
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(hideBubble, 6000);
  }

  function hideBubble() {
    bubble.classList.add("hidden");
    bubbleText.textContent = "";
    resizeTo(IDLE);
    clearTimeout(bubbleTimer);
  }

  /* ---------- SSE：chat 事件驱动说话辉光 + 气泡 ---------- */

  async function connectEvents() {
    if (!TAURI) return;
    let port;
    try {
      port = await TAURI.core.invoke("get_port");
    } catch (err) {
      console.error("get_port failed", err);
      return;
    }
    const es = new EventSource("http://127.0.0.1:" + port + "/api/events");
    es.onmessage = function (evt) {
      let msg;
      try {
        msg = JSON.parse(evt.data);
      } catch (_) {
        return;
      }
      if (!msg || msg.channel !== "chat") return;
      const p = msg.payload || {};
      if (p.state === "start") {
        glow.classList.add("speaking");
        resizeTo(BUBBLE);
      } else if (p.state === "done") {
        glow.classList.remove("speaking");
        if (p.text) {
          showBubble(p.text);
        } else {
          resizeTo(IDLE);
        }
      }
    };
    // EventSource 自动重连，无需额外处理
  }

  /* ---------- 拖动 vs 点击 ---------- */

  let dragStart = null;
  let moved = false;

  window.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    // 菜单内的操作不触发拖动/点击唤起
    if (e.target.closest("#ctx")) return;
    dragStart = { x: e.screenX, y: e.screenY };
    moved = false;
  });

  window.addEventListener("pointermove", (e) => {
    // 分部件朝向光标：身体小幅整体转向（以底为轴），头部在颈部关节上更灵活追视。
    // 拖动中 / 气泡展示时不跟随，避免乱转。
    if (!document.body.classList.contains("dragging") && bubble.classList.contains("hidden")) {
      const rect = $("stage").getBoundingClientRect();
      const cx = rect.width / 2;
      const cy = rect.height;
      let dx = (e.clientX - cx) / cx;
      let dy = (e.clientY - cy) / cy;
      dx = Math.max(-1, Math.min(1, dx));
      dy = Math.max(-1, Math.min(1, dy));
      body.style.transform =
        "rotateY(" + (dx * 4).toFixed(2) + "deg) rotateX(" + (-dy * 2).toFixed(2) + "deg)";
      head.style.transform =
        "rotateY(" + (dx * 9).toFixed(2) + "deg) rotateX(" + (-dy * 5).toFixed(2) + "deg) translate(" +
        (dx * 1.5).toFixed(2) + "px, " + (dy * 2).toFixed(2) + "px)";
    }

    // 拖动阈值判断
    if (!dragStart || moved) return;
    const dist = Math.hypot(e.screenX - dragStart.x, e.screenY - dragStart.y);
    if (dist <= 4) return;

    moved = true;
    if (TAURI) {
      document.body.classList.add("dragging");
      (async () => {
        const { window: winApi } = TAURI;
        const win = winApi.getCurrentWindow();
        try {
          await win.startDragging();
          const pos = await win.outerPosition();
          await TAURI.core.invoke("set_pet_pos", { x: pos.x, y: pos.y });
        } catch (err) {
          console.warn("drag failed", err);
        } finally {
          document.body.classList.remove("dragging");
        }
        dragStart = null;
      })();
    }
  });

  window.addEventListener("pointerup", () => {
    if (dragStart && !moved && TAURI) {
      // 原地点击 → 唤起 HUD（菜单打开时点击空白仅关菜单，不误触）
      if (ctx.classList.contains("hidden")) {
        TAURI.core.invoke("open_hud").catch((err) => console.warn(err));
      }
    }
    dragStart = null;
  });

  document.documentElement.addEventListener("pointerleave", () => {
    body.style.transform = "";
    head.style.transform = "";
  });

  /* ---------- 右键菜单 ---------- */

  document.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (!TAURI) return;
    const mx = Math.min(e.clientX, document.documentElement.clientWidth - 132);
    const my = Math.min(e.clientY, document.documentElement.clientHeight - 108);
    ctx.style.left = mx + "px";
    ctx.style.top = my + "px";
    ctx.classList.remove("hidden");
  });

  document.addEventListener("click", (e) => {
    if (!ctx.contains(e.target)) ctx.classList.add("hidden");
  });

  ctx.addEventListener("click", (e) => {
    const act = e.target.getAttribute("data-act");
    if (act === "open_hud" && TAURI) {
      TAURI.core.invoke("open_hud").catch((err) => console.warn(err));
    } else if (act === "quit" && TAURI) {
      TAURI.core.invoke("quit_app").catch((err) => console.warn(err));
    }
    ctx.classList.add("hidden");
  });

  /* ---------- 启动 ---------- */

  connectEvents();
  if (!TAURI) {
    console.log("pet: 纯浏览器预览模式（无 Tauri IPC）");
  } else {
    // WebView2 创建时会把窗口撑到约 136px 最小宽度；启动后归一一次到配置尺寸，
    // 位置不变（resizeTo 保持底边锚定），桌宠贴边时能更贴近屏幕边缘。
    resizeTo(IDLE);
  }
})();
