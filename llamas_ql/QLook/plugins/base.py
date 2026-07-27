"""Plugin contract for Quick Look tools.

A plugin contributes a dockable widget and gets a :class:`ToolContext` giving it
the live :class:`Session` (current RSS/cube/selection + change signals), the
:class:`NightPaths`, the main window (to add docks/menus), and ``run_job`` to
launch backend work through the same subprocess/IPC path the core uses.

Example (an external PyAPS package)::

    class PyAPSPlugin(QLToolPlugin):
        id = "pyaps"
        title = "PyAPS"
        requires = ("rss",)

        def create_widget(self, ctx):
            w = PyAPSPanel()
            ctx.session.rss_loaded.connect(lambda rss: w.set_input(rss))
            w.run_requested.connect(
                lambda: ctx.run_job(JobSpec(kind="plugin", plugin_id="pyaps"))
            )
            return w

    # in its pyproject.toml:
    # [project.entry-points."llamas_ql.qlook_plugins"]
    # pyaps = "pyaps_qlook:PyAPSPlugin"
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from PySide6.QtWidgets import QWidget

#: Entry-point group external packages register plugins under.
ENTRY_POINT_GROUP = "llamas_ql.qlook_plugins"


@dataclass
class ToolContext:
    """Everything a plugin needs to integrate, passed at widget-creation time."""

    session: object          # llamas_ql.QLook.core.session.Session
    main_window: object      # llamas_ql.QLook.main_window.MainWindow
    paths: object            # llamas_ql.QLook.core.paths.NightPaths
    run_job: Callable        # (JobSpec) -> ReductionProcess


class QLToolPlugin(ABC):
    """Base class for Quick Look tool plugins."""

    #: Unique, stable identifier (e.g. ``"pyaps"``).
    id: str = "plugin"
    #: Human-readable label shown in the Tools menu.
    title: str = "Tool"
    #: Capability tags the tool needs (informational), e.g. ``("rss",)``/``("cube",)``.
    requires: tuple[str, ...] = ()

    @abstractmethod
    def create_widget(self, ctx: ToolContext) -> QWidget:
        """Build and return the plugin's widget; called when first activated."""

    def on_register(self, ctx: ToolContext) -> None:  # optional hook
        """Called once when the plugin is registered (e.g. to add menu actions)."""
