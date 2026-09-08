# -*- coding: utf-8 -*-
"""
desktop/scripts/prep_pet.py — 桌宠透明抠图
==========================================
方法 A（默认，零新依赖）：亮度键抠图 + 去偏色
方法 B（兜底）：rembg AI 抠图（需 pip install rembg）

用法：
  python prep_pet.py                 # 方法 A → desktop/ui/pet.png
  python prep_pet.py --method rembg  # 方法 B
  python prep_pet.py --checker       # 额外生成桌面预览图（白底棋盘格）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent  # desktop/
SRC = Path(os.getenv("JARVIS_PET_SOURCE", str(ROOT / "assets" / "pet-source.jpg")))
OUT = ROOT / "ui" / "pet.png"
PREVIEW = ROOT / "ui" / "pet_preview.png"
TARGET_H = 150  # 桌宠约 140px 高，稍留余量


def luminance_key_np(img: Image.Image, t_lo: int = 58, t_hi: int = 112) -> Image.Image:
    """用 numpy 做亮度键（numpy 在构建 venv 与运行 venv 均可用）。"""
    import numpy as np
    arr = np.asarray(img.convert("RGBA")).astype(np.int16)
    lum = (0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2])
    alpha = np.clip((lum - t_lo) / (t_hi - t_lo) * 255, 0, 255).astype(np.uint8)
    # 去偏色：把接近全透明的像素颜色压向白色，避免深色边缘晕
    spill = (alpha < 60)
    arr[spill, 0] = np.clip(arr[spill, 0] * 1.4, 0, 255)
    arr[spill, 1] = np.clip(arr[spill, 1] * 1.4, 0, 255)
    arr[spill, 2] = np.clip(arr[spill, 2] * 1.4, 0, 255)
    out = Image.fromarray(np.dstack([arr[..., 0], arr[..., 1], arr[..., 2], alpha]).astype(np.uint8), "RGBA")
    return out


def trim_and_resize(img: Image.Image, target_h: int = TARGET_H, pad: int = 6) -> Image.Image:
    """裁到内容包围盒、填充小孔洞、按高度等比缩放，并四周留白。"""
    img = fill_small_holes(img)
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    w, h = img.size
    nw = max(1, round(w * target_h / h))
    img = img.resize((nw, target_h), Image.LANCZOS)
    # 四周留白，避免精灵贴窗口边缘
    canvas = Image.new("RGBA", (nw + pad * 2, target_h + pad * 2), (0, 0, 0, 0))
    canvas.paste(img, (pad, pad), img)
    return canvas


def fill_small_holes(img: Image.Image, iters: int = 3) -> Image.Image:
    """把被不透明像素完全包围的透明像素填成不透明（迭代，消除装甲缝隙孔洞）。"""
    import numpy as np
    arr = np.asarray(img.convert("RGBA"))
    a = arr[..., 3]
    for _ in range(iters):
        op = a > 128
        # 4-邻域全不透明 → 该点填实
        n = np.zeros_like(op, dtype=np.int32)
        n[1:, :] += op[:-1, :]; n[:-1, :] += op[1:, :]
        n[:, 1:] += op[:, :-1]; n[:, :-1] += op[:, 1:]
        fill = (~op) & (n >= 4)
        if not fill.any():
            break
        a[fill] = 255
        arr[..., 3] = a
    return Image.fromarray(arr, "RGBA")


def checker_preview(img: Image.Image, out: Path) -> None:
    """白底 + 棋盘格预览，用于目检透明边缘。"""
    w, h = img.size
    pad = 24
    canvas = Image.new("RGB", (w + pad * 2, h + pad * 2), (240, 240, 240))
    px = canvas.load()
    for y in range(canvas.size[1]):
        for x in range(canvas.size[0]):
            if ((x // 24) + (y // 24)) % 2 == 0:
                px[x, y] = (255, 255, 255)
            else:
                px[x, y] = (210, 215, 220)
    canvas.paste(img, (pad, pad), img)
    canvas.save(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", choices=["luma", "rembg"], default="luma")
    ap.add_argument("--checker", action="store_true", help="生成棋盘格预览图")
    ap.add_argument("--t-lo", type=int, default=58)
    ap.add_argument("--t-hi", type=int, default=112)
    args = ap.parse_args()

    if not SRC.exists():
        print(f"[prep_pet] 源图不存在: {SRC}", file=sys.stderr)
        return 1

    img = Image.open(SRC).convert("RGBA")

    if args.method == "rembg":
        try:
            from rembg import remove
            print("[prep_pet] 使用 rembg 抠图...")
            cut = remove(img)
            cut = cut.convert("RGBA")
        except Exception as e:  # pragma: no cover
            print(f"[prep_pet] rembg 失败({e})，回退亮度键", file=sys.stderr)
            cut = luminance_key_np(img, args.t_lo, args.t_hi)
    else:
        print(f"[prep_pet] 亮度键抠图 t_lo={args.t_lo} t_hi={args.t_hi} ...")
        cut = luminance_key_np(img, args.t_lo, args.t_hi)

    cut = trim_and_resize(cut)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cut.save(OUT)
    print(f"[prep_pet] 已输出 {OUT}  ({cut.size[0]}x{cut.size[1]})")

    if args.checker:
        checker_preview(cut, PREVIEW)
        print(f"[prep_pet] 棋盘格预览 {PREVIEW}")

    # 统计透明度，便于程序化判断
    import numpy as np
    a = np.asarray(cut)[..., 3]
    opaque = int((a > 200).sum())
    semi = int(((a > 0) & (a <= 200)).sum())
    total = a.size
    print(f"[prep_pet] 统计: 不透明 {opaque} / 半透明 {semi} / 全透明 {total-opaque-semi} (总 {total})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
