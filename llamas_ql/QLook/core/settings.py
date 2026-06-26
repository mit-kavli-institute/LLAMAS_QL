"""Persistent GUI preferences via QSettings.

Remembers the last-used directories and window geometry across sessions so the
observer doesn't re-navigate every night.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

_ORG = "LLAMAS"
_APP = "QuickLook"


def settings() -> QSettings:
    return QSettings(_ORG, _APP)


def get_str(key: str, default: str = "") -> str:
    val = settings().value(key, default)
    return str(val) if val is not None else default


def set_str(key: str, value: str) -> None:
    settings().setValue(key, value)


def get_bytes(key: str):
    return settings().value(key)


def set_bytes(key: str, value) -> None:
    settings().setValue(key, value)
