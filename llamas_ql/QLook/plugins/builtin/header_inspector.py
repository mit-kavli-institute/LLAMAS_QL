"""Reference built-in plugin: show the current RSS primary header.

Demonstrates the plugin contract end to end — it builds a widget, subscribes to
``session.rss_loaded`` for live updates, and reads the current data product.
"""

from __future__ import annotations

from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from llamas_ql.QLook.plugins.base import QLToolPlugin, ToolContext


class _HeaderWidget(QWidget):
    def __init__(self, ctx: ToolContext):
        super().__init__()
        self._ctx = ctx
        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setStyleSheet("font-family: monospace;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self._text)

        ctx.session.rss_loaded.connect(self._update)
        self._update(ctx.session.current_rss)

    def _update(self, rss) -> None:
        if rss is None or rss.primary_header is None:
            self._text.setPlainText("(no RSS loaded)")
            return
        self._text.setPlainText(repr(rss.primary_header))


class HeaderInspectorPlugin(QLToolPlugin):
    id = "header_inspector"
    title = "Header Inspector"
    requires = ("rss",)

    def create_widget(self, ctx: ToolContext) -> QWidget:
        return _HeaderWidget(ctx)
