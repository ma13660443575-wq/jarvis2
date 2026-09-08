# -*- coding: utf-8 -*-
"""受限 tar 解压工具。

模型包来自网络，即使下载地址固定，也不能直接对归档调用 extractall：
归档成员可能通过 ``..``、绝对路径、软链接或设备文件逃出目标目录。
"""
from __future__ import annotations

import tarfile
from pathlib import Path, PureWindowsPath


MAX_MEMBERS = 10_000
MAX_BYTES = 1024 * 1024 * 1024


def safe_extract(tf: tarfile.TarFile, destination: str | Path) -> None:
    """校验并解压普通文件/目录成员到 destination。"""
    root = Path(destination).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    members = tf.getmembers()
    if len(members) > MAX_MEMBERS:
        raise ValueError(f"归档成员过多（{len(members)} > {MAX_MEMBERS}）")

    total_bytes = 0
    for member in members:
        raw_name = str(member.name or "")
        normalized = raw_name.replace("\\", "/")
        if (not normalized or normalized.startswith("/") or
                PureWindowsPath(normalized).drive):
            raise ValueError(f"归档包含绝对路径成员: {raw_name}")

        target = (root / Path(normalized)).resolve()
        try:
            target.relative_to(root)
        except ValueError as e:
            raise ValueError(f"归档成员越界: {raw_name}") from e

        # 即使归档本身没有链接，也不能让它沿用目标目录里已有的软链接目录。
        cursor = root
        for part in Path(normalized).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError(f"目标目录包含软链接，拒绝覆盖: {raw_name}")

        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise ValueError(f"归档包含不允许的链接或特殊文件: {raw_name}")
        if not member.isfile() and not member.isdir():
            raise ValueError(f"归档包含未知文件类型: {raw_name}")
        if member.isfile():
            total_bytes += max(0, int(member.size or 0))
            if total_bytes > MAX_BYTES:
                raise ValueError(f"归档展开内容过大（>{MAX_BYTES} bytes）")

    # Python 3.12+ 的 data filter 会再次拒绝危险成员；旧版本已完成上面的
    # 路径、链接和特殊文件校验，再使用兼容调用。
    if hasattr(tarfile, "data_filter"):
        tf.extractall(root, members=members, filter="data")
    else:
        tf.extractall(root, members=members)
