"""Exposure browser model: scan a night directory for science FITS frames.

``ExposureScanner`` is Qt-free and does the filesystem + minimal-header work
(cached by mtime).  ``ExposureTableModel`` adapts the scan results to a Qt view.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass

from astropy.io import fits
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


@dataclass
class ExposureEntry:
    path: str
    filename: str
    obj: str
    exptime: float | None
    mtime: float
    is_new: bool = False  # set by the scanner when first seen after a refresh

    @property
    def mtime_str(self) -> str:
        import datetime

        return datetime.datetime.fromtimestamp(self.mtime).strftime("%H:%M:%S")


# Header keywords we try, in order, for the displayed object label and exptime.
_OBJ_KEYS = ("OBJECT", "OBJNAME", "TARGET")
_EXPTIME_KEYS = ("EXPTIME", "EXPOSURE", "EXPTIMES")


class ExposureScanner:
    """Globs a directory for FITS exposures and reads a few header fields.

    Results are cached by ``(path, mtime)`` so repeated refreshes are cheap; only
    new or changed files are re-read.  ``scan`` flags entries not seen in the
    previous scan as ``is_new`` so the view can highlight fresh exposures.
    """

    def __init__(self):
        self._cache: dict[str, ExposureEntry] = {}
        self._seen_paths: set[str] = set()

    def scan(self, directory: str) -> list[ExposureEntry]:
        if not directory or not os.path.isdir(directory):
            return []
        paths = sorted(
            glob.glob(os.path.join(directory, "*.fits"))
            + glob.glob(os.path.join(directory, "*.fit"))
        )
        previous = self._seen_paths
        entries: list[ExposureEntry] = []
        current: set[str] = set()
        for p in paths:
            current.add(p)
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                continue
            cached = self._cache.get(p)
            if cached is None or cached.mtime != mtime:
                entry = self._read_entry(p, mtime)
                self._cache[p] = entry
            else:
                entry = cached
            entry.is_new = p not in previous
            entries.append(entry)
        self._seen_paths = current
        return entries

    @staticmethod
    def _read_entry(path: str, mtime: float) -> ExposureEntry:
        obj = ""
        exptime: float | None = None
        try:
            hdr = fits.getheader(path, ext=0)
            for k in _OBJ_KEYS:
                if k in hdr:
                    obj = str(hdr[k])
                    break
            for k in _EXPTIME_KEYS:
                if k in hdr:
                    try:
                        exptime = float(hdr[k])
                    except (TypeError, ValueError):
                        pass
                    break
        except Exception:
            pass
        return ExposureEntry(
            path=path, filename=os.path.basename(path), obj=obj,
            exptime=exptime, mtime=mtime,
        )


class ExposureTableModel(QAbstractTableModel):
    _HEADERS = ("File", "Object", "Exp (s)", "Time")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[ExposureEntry] = []

    def set_entries(self, entries: list[ExposureEntry]) -> None:
        self.beginResetModel()
        self._rows = entries
        self.endResetModel()

    def entry_at(self, row: int) -> ExposureEntry | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    # -- Qt model API --------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        entry = self._rows[index.row()]
        if role == Qt.DisplayRole:
            col = index.column()
            if col == 0:
                return entry.filename
            if col == 1:
                return entry.obj
            if col == 2:
                return "" if entry.exptime is None else f"{entry.exptime:g}"
            if col == 3:
                return entry.mtime_str
        elif role == Qt.FontRole and entry.is_new:
            from PySide6.QtGui import QFont

            font = QFont()
            font.setBold(True)
            return font
        return None

    def headerData(self, section: int, orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self._HEADERS[section]
        return None
