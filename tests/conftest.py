"""Shared pytest setup: run Qt headless (offscreen) with the PySide6 plugin."""
import os

import PySide6

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_platforms = os.path.join(os.path.dirname(PySide6.__file__), "Qt", "plugins", "platforms")
os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", _platforms)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
