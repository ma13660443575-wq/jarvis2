# -*- coding: utf-8 -*-
"""Deterministic parsing for the Phase-A Save-As continuation.

This module only parses user-provided destination intent.  It does not resolve
known folders, touch the filesystem, or operate a Windows file dialog.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import ntpath
import re
from typing import Any


class SaveDestinationStatus(str, Enum):
    """Outcome of deterministic Save-As destination parsing."""

    PARSED = "PARSED"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"
    NOT_RELEVANT = "NOT_RELEVANT"


@dataclass(frozen=True)
class SaveDestination:
    """A safe, unresolved Save-As destination description.

    ``directory`` is symbolic for ``source_kind == known_folder`` and is not
    resolved to a local path until a future File Dialog execution phase.  The
    model intentionally contains no original user sentence.
    """

    directory: str | None = None
    filename: str | None = None
    full_path: str | None = None
    source_kind: str = ""
    overwrite_policy: str = "deny_or_confirm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": self.directory,
            "filename": self.filename,
            "full_path": self.full_path,
            "source_kind": self.source_kind,
            "overwrite_policy": self.overwrite_policy,
        }

    def display_label(self) -> str:
        """Return a deterministic, non-claiming label for user feedback."""
        if self.full_path:
            return self.full_path

        directory_labels = {
            "desktop": "桌面",
            "documents": "文档",
        }
        if self.source_kind == "known_folder":
            directory = directory_labels.get(self.directory or "", self.directory or "已选目录")
        elif self.source_kind == "current_dialog_directory":
            directory = "当前目录"
        elif self.directory:
            directory = self.directory
        else:
            directory = "当前目录"

        if self.filename:
            return f"{directory}\\{self.filename}"
        return directory


@dataclass(frozen=True)
class SaveDestinationParseResult:
    status: SaveDestinationStatus
    destination: SaveDestination | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "destination": self.destination.to_dict() if self.destination else None,
            "reason": self.reason,
        }


_KNOWN_FOLDERS = {
    "桌面": "desktop",
    "desktop": "desktop",
    "文档": "documents",
    "documents": "documents",
}

_CURRENT_DIRECTORY_ALIASES = {
    "当前目录",
    "current directory",
}

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_RESERVED_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())


def _strip_wrapping_quotes(value: str) -> str:
    pairs = (("\"", "\""), ("'", "'"), ("「", "」"), ("“", "”"), ("‘", "’"))
    for left, right in pairs:
        if value.startswith(left) and value.endswith(right) and len(value) >= 2:
            return value[1:-1].strip()
    return value.strip()


def _invalid_filename_reason(filename: str) -> str | None:
    if not filename:
        return "文件名不能为空"
    if filename in {".", ".."}:
        return "文件名不能是路径占位符"
    if _CONTROL_CHARACTERS.search(filename):
        return "文件名包含控制字符"
    if _INVALID_FILENAME_CHARS.search(filename):
        return "文件名包含 Windows 非法字符"
    if filename.endswith((" ", ".")):
        return "文件名不能以空格或句点结尾"
    stem = filename.split(".", 1)[0].upper()
    if stem in _RESERVED_DEVICE_NAMES:
        return "文件名是 Windows 保留设备名"
    return None


def _invalid_directory_reason(directory: str) -> str | None:
    if _CONTROL_CHARACTERS.search(directory):
        return "目录包含控制字符"
    if any(char in directory for char in '<>"|?*'):
        return "目录包含 Windows 非法字符"
    drive, tail = ntpath.splitdrive(directory)
    if not drive or not tail.startswith(("\\", "/")):
        return "目录不是 Windows 绝对路径"
    for component in re.split(r"[\\/]+", tail):
        if not component:
            continue
        reason = _invalid_filename_reason(component)
        if reason:
            return f"目录包含非法部分：{component}"
    return None


def _normalize_absolute_path(value: str) -> tuple[str | None, str | None]:
    value = _strip_wrapping_quotes(value)
    if not _DRIVE_ABSOLUTE.match(value):
        return None, "只支持 Windows 本地绝对路径"
    if value.startswith(("\\\\", "//")) or "://" in value:
        return None, "不支持网络路径或 URL"
    if _CONTROL_CHARACTERS.search(value):
        return None, "路径包含控制字符"
    normalized = ntpath.normpath(value)
    reason = _invalid_directory_reason(normalized)
    if reason:
        return None, reason
    return normalized, None


def _is_filename_like(value: str) -> bool:
    """Use an explicit extension as the conservative full-path signal."""
    return "." in value and not value.endswith(".")


def _result(
    status: SaveDestinationStatus,
    destination: SaveDestination | None = None,
    reason: str = "",
) -> SaveDestinationParseResult:
    return SaveDestinationParseResult(status=status, destination=destination, reason=reason)


def _destination_for_directory(
    directory: str,
    *,
    filename: str | None,
    source_kind: str,
) -> SaveDestinationParseResult:
    if filename is not None:
        filename = _strip_wrapping_quotes(filename)
        reason = _invalid_filename_reason(filename)
        if reason:
            return _result(SaveDestinationStatus.INVALID, reason=reason)
        destination = SaveDestination(
            directory=directory,
            filename=filename,
            source_kind=source_kind,
        )
        return _result(SaveDestinationStatus.PARSED, destination)

    destination = SaveDestination(directory=directory, source_kind=source_kind)
    return _result(
        SaveDestinationStatus.INCOMPLETE,
        destination,
        reason="已识别保存目录，但还需要文件名",
    )


def _parse_absolute_location(location: str, filename: str | None) -> SaveDestinationParseResult:
    normalized, reason = _normalize_absolute_path(location)
    if normalized is None:
        return _result(SaveDestinationStatus.INVALID, reason=reason or "绝对路径无效")

    if filename is not None:
        # An explicit filename makes the parsed location a directory.  This
        # avoids ever appending a second filename to a path supplied by the
        # user and keeps Phase A side-effect free.
        return _destination_for_directory(
            normalized,
            filename=filename,
            source_kind="absolute_path",
        )

    directory, leaf = ntpath.split(normalized)
    if _is_filename_like(leaf):
        filename_reason = _invalid_filename_reason(leaf)
        if filename_reason:
            return _result(SaveDestinationStatus.INVALID, reason=filename_reason)
        return _result(
            SaveDestinationStatus.PARSED,
            SaveDestination(
                directory=directory or None,
                filename=leaf,
                full_path=normalized,
                source_kind="absolute_path",
            ),
        )

    return _result(
        SaveDestinationStatus.INCOMPLETE,
        SaveDestination(directory=normalized, source_kind="absolute_path"),
        reason="已识别绝对目录，但还需要文件名",
    )


def _parse_location(location: str, filename: str | None) -> SaveDestinationParseResult:
    location = _strip_wrapping_quotes(location)
    if not location:
        return _result(SaveDestinationStatus.INVALID, reason="保存位置不能为空")

    known_folder = _KNOWN_FOLDERS.get(location.casefold())
    if known_folder:
        return _destination_for_directory(
            known_folder,
            filename=filename,
            source_kind="known_folder",
        )

    if location.casefold() in {item.casefold() for item in _CURRENT_DIRECTORY_ALIASES}:
        return _destination_for_directory(
            "current_dialog_directory",
            filename=filename,
            source_kind="current_dialog_directory",
        )

    if _DRIVE_ABSOLUTE.match(location):
        return _parse_absolute_location(location, filename)

    return _result(
        SaveDestinationStatus.INVALID,
        reason="暂不支持该保存位置；请使用桌面、文档或 Windows 绝对路径",
    )


def _split_save_location(text: str) -> tuple[str, str | None] | None:
    body = re.sub(r"^(?:就)?保存到\s*", "", text, count=1, flags=re.IGNORECASE)
    if not body or body == text:
        return None

    explicit = re.match(
        r"^(?P<location>.+?)[，,]\s*(?:(?:文件名|文件)\s*)?(?:叫|为|是)\s*(?P<filename>.+)$",
        body,
        flags=re.IGNORECASE,
    )
    if explicit:
        return explicit.group("location").strip(), explicit.group("filename").strip()
    return body.strip(), None


def parse_save_destination(text: object) -> SaveDestinationParseResult:
    """Parse only explicit Save-As destination language.

    The parser deliberately returns ``NOT_RELEVANT`` for ordinary discussion
    and never calls a planner, an LLM, Windows APIs, or the filesystem.
    """
    normalized = _normalized_text(text)
    if not normalized:
        return _result(SaveDestinationStatus.NOT_RELEVANT)

    filename_only = re.match(
        r"^文件名\s*(?:叫|为|是)\s*(?P<filename>.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if filename_only:
        filename = _strip_wrapping_quotes(filename_only.group("filename"))
        reason = _invalid_filename_reason(filename)
        if reason:
            return _result(SaveDestinationStatus.INVALID, reason=reason)
        return _result(
            SaveDestinationStatus.PARSED,
            SaveDestination(filename=filename, source_kind="filename_only"),
        )

    location_parts = _split_save_location(normalized)
    if location_parts is None:
        return _result(SaveDestinationStatus.NOT_RELEVANT)
    location, filename = location_parts
    return _parse_location(location, filename)
