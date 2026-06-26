"""LLAMAS Quick Look GUI (PySide6).

This subpackage holds the observer-facing front-end for the LLAMAS Quick Look
reduction engine.  Importing ``llamas_ql`` does *not* import this package, so the
headless pipeline stays free of any Qt dependency.  Only ``llamas_ql.QLook`` (and
its submodules) require the optional ``gui`` extra (PySide6 + pyqtgraph).

Launch with the ``llamas-ql-gui`` console script or ``python -m llamas_ql.QLook.app``.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
