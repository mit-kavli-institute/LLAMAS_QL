"""Application bootstrap for the LLAMAS Quick Look GUI.

Entry points:
  * console script ``llamas-ql-gui`` -> :func:`main`
  * ``python -m llamas_ql.QLook.app``
"""

from __future__ import annotations

import os
import sys


def _ensure_qt_plugin_path() -> None:
    """Pin Qt to PySide6's own bundled plugins.

    Conda environments commonly ship a *separate* Qt (e.g. ``lib/qt6`` at a
    different minor version) and export its plugin directory.  Qt would then try
    that version-mismatched ``platforms`` plugin first and fail to start with
    "Could not find the Qt platform plugin".  Prepending PySide6's own plugin
    directory to ``QT_PLUGIN_PATH`` before ``QApplication`` is constructed makes
    the matching plugins win, so the GUI launches regardless of a conda Qt.
    """
    try:
        import PySide6
    except ImportError:
        return
    plugins = os.path.join(os.path.dirname(PySide6.__file__), "Qt", "plugins")
    if not os.path.isdir(plugins):
        return
    existing = os.environ.get("QT_PLUGIN_PATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    if plugins not in parts:
        os.environ["QT_PLUGIN_PATH"] = os.pathsep.join([plugins, *parts])


def main(argv: list[str] | None = None) -> int:
    # Pin the plugin path before *any* QApplication is constructed.
    _ensure_qt_plugin_path()

    # Import Qt lazily inside main() so a bare ``import llamas_ql.QLook.app``
    # for introspection doesn't hard-require PySide6 being installed.
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:  # pragma: no cover - environment guard
        sys.stderr.write(
            "The Quick Look GUI requires the optional 'gui' extra.\n"
            "Install it with:  pip install -e '.[gui]'   (or  poetry install -E gui)\n"
            f"Original import error: {exc}\n"
        )
        return 1

    from llamas_ql.QLook.main_window import MainWindow

    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("LLAMAS Quick Look")
    app.setOrganizationName("LLAMAS")

    # Apply the light design-system theme app-wide before any widgets build.
    from llamas_ql.QLook.core import theme

    app.setFont(theme.sans_font(12))
    app.setStyleSheet(theme.stylesheet())

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
